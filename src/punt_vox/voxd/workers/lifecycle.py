"""Provider-neutral lifecycle decisions return new immutable checkpoints."""

from __future__ import annotations

import logging
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Self, final

from punt_vox.voxd.workers.contracts import HarnessProfile, WorkerProvider
from punt_vox.voxd.workers.model import WorkerRecord

logger = logging.getLogger(__name__)


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

    def observe(self, record: WorkerRecord) -> WorkerRecord:
        """Reconcile liveness without inventing readiness or suppressing new reports."""
        if (
            record.state in ("stopped", "exited")
            or "launch_error" in record.last_report
        ):
            return record
        alive = self._alive(record)
        if alive is None:
            return record
        return self.report(record) if alive else record.retire()

    def _alive(self, record: WorkerRecord) -> bool | None:
        try:
            return self._providers[record.provider].status(record.session)
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            logger.warning("Worker provider unavailable (%s)", type(exc).__name__)
            return None

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
        return self._cleanup(replace(record, last_report=report))

    def _cleanup(self, record: WorkerRecord) -> WorkerRecord:
        try:
            self._profiles[record.harness].cleanup(Path(record.worktree))
        except (
            KeyError,
            OSError,
            ValueError,
            RuntimeError,
            subprocess.SubprocessError,
        ) as exc:
            logger.warning("Worker cleanup failed (%s)", type(exc).__name__)
            return replace(
                record,
                last_report={**record.last_report, "cleanup_error": type(exc).__name__},
            )
        return replace(
            record,
            last_report={
                k: v
                for k, v in record.last_report.items()
                if k not in ("cleanup_error", "cleanup_pending")
            },
        )

    def stop(self, record: WorkerRecord) -> WorkerRecord:
        """Terminal phase is stable, but failed cleanup remains retryable."""
        if record.state in ("stopped", "exited"):
            return (
                self._cleanup(record)
                if {"cleanup_error", "cleanup_pending"} & record.last_report.keys()
                else record
            )
        dead = "launch_error" not in record.last_report and self._alive(record) is False
        self._providers[record.provider].stop(record.session)
        return self._cleanup(
            replace(
                record,
                state="exited" if dead else "stopped",
                last_report={
                    k: v for k, v in record.last_report.items() if k != "stop_error"
                },
            )
        )
