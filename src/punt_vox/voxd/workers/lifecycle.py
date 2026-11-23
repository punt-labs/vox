"""Provider-neutral lifecycle decisions return new immutable checkpoints."""

from __future__ import annotations

import logging
import subprocess
from dataclasses import replace
from enum import Enum, auto
from typing import Self, final

from punt_vox.voxd.workers.cleanup import WorkerCleanup
from punt_vox.voxd.workers.contracts import HarnessProfile, WorkerProvider
from punt_vox.voxd.workers.model import WorkerRecord

logger = logging.getLogger(__name__)


class WorkerLiveness(Enum):
    """Distinguish provider uncertainty from a confirmed exit."""

    ALIVE = auto()
    EXITED = auto()
    UNKNOWN = auto()


@final
class WorkerLifecycle:
    """Control provider resources separately from durable registry ownership."""

    __slots__ = ("_profiles", "_providers")
    _profiles: dict[str, HarnessProfile]
    _providers: dict[str, WorkerProvider]

    def __new__(
        cls, providers: dict[str, WorkerProvider], profiles: dict[str, HarnessProfile]
    ) -> Self:
        """Bind the same open catalogs used for launch."""
        self = super().__new__(cls)
        self._providers, self._profiles = providers, profiles
        return self

    def observe(self, record: WorkerRecord) -> tuple[WorkerRecord, bool]:
        """Return the snapshot and whether the provider confirmed a live pane."""
        if (
            record.state in ("stopped", "exited")
            or "launch_error" in record.last_report
        ):
            return record, False
        match self.probe(record, self._providers[record.provider]):
            case WorkerLiveness.ALIVE:
                return self.report(record), True
            case WorkerLiveness.EXITED:
                return record.retire(), False
            case WorkerLiveness.UNKNOWN:
                return record, False

    @staticmethod
    def probe(record: WorkerRecord, provider: WorkerProvider) -> WorkerLiveness:
        """Use one liveness classification for both boot and worker control."""
        try:
            alive = provider.status(record.session)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            logger.warning(
                "Worker %s provider unavailable (%s)", record.id, type(exc).__name__
            )
            return WorkerLiveness.UNKNOWN
        return WorkerLiveness.ALIVE if alive else WorkerLiveness.EXITED

    def report(self, record: WorkerRecord) -> WorkerRecord:
        """Replace a fresh snapshot without regressing a previously ready phase."""
        report = self._providers[record.provider].progress(
            record.id, self._profiles[record.harness]
        )
        if record.state == "ready" and (
            not report or report == {"session_started": False}
        ):
            return record
        return replace(
            record,
            last_report=report,
            state="ready" if report.get("session_started") else record.state,
        )

    def rollback(self, record: WorkerRecord, error: Exception) -> WorkerRecord:
        """Preserve launch failure while attempting termination and trust cleanup."""
        report = {**record.last_report, "launch_error": type(error).__name__}
        try:
            self._providers[record.provider].stop(record.session)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            report["stop_error"] = type(exc).__name__
            logger.warning(
                "Worker rollback termination failed (%s)", type(exc).__name__
            )
        else:
            record = replace(record, state="stopped")
        return WorkerCleanup.run(replace(record, last_report=report), self._profiles)

    def stop(self, record: WorkerRecord) -> WorkerRecord:
        """Terminal phase is stable, but failed cleanup remains retryable."""
        if record.state == "exited":
            self._providers[record.provider].stop(record.session)
        if record.state in ("stopped", "exited"):
            return (
                WorkerCleanup.run(record, self._profiles)
                if WorkerCleanup.pending(record)
                else record
            )
        dead = (
            "launch_error" not in record.last_report
            and self.probe(record, self._providers[record.provider])
            is WorkerLiveness.EXITED
        )
        self._providers[record.provider].stop(record.session)
        return WorkerCleanup.run(
            replace(
                record,
                state="exited" if dead else "stopped",
                last_report={
                    k: v for k, v in record.last_report.items() if k != "stop_error"
                },
            ),
            self._profiles,
        )
