"""Schedule trust cleanup independently of worker snapshots and hook delivery."""

from __future__ import annotations

import logging
import subprocess
from dataclasses import replace
from typing import TYPE_CHECKING, Self, final

from punt_vox.voxd.workers.cleanup import WorkerCleanup
from punt_vox.voxd.workers.cleanup_queue import WorkerCleanupQueue

if TYPE_CHECKING:
    from collections.abc import Callable
    from concurrent.futures import Future

    from punt_vox.voxd.workers.contracts import HarnessProfile
    from punt_vox.voxd.workers.inventory import WorkerInventory
    from punt_vox.voxd.workers.lifecycle import WorkerLifecycle
    from punt_vox.voxd.workers.model import WorkerRecord

logger = logging.getLogger(__name__)


@final
class WorkerRetirement:
    """Run terminal cleanup once per worker and checkpoint its outcome."""

    __slots__ = ("_executor", "_inventory", "_profiles", "_release")
    _executor: WorkerCleanupQueue
    _inventory: WorkerInventory
    _profiles: dict[str, HarnessProfile]
    _release: Callable[[str], None]

    def __new__(
        cls, profiles: dict[str, HarnessProfile], inventory: WorkerInventory
    ) -> Self:
        """Use one background writer for harness cleanup."""
        self = super().__new__(cls)
        self._profiles = profiles
        self._inventory = inventory
        self._executor = WorkerCleanupQueue()
        self._release = lambda _worker: None
        return self

    def release_with(self, release: Callable[[str], None]) -> None:
        """Bind the actor-owned retention transition, not a cleanup job."""
        self._release = release

    def schedule(
        self, worker_id: str
    ) -> Future[None] | None:  # PY-TS-14: no future without a newly queued job.
        """Queue terminal cleanup without waiting for a config file lease."""
        record = self._inventory.get(worker_id)
        if record.state not in ("exited", "stopped") or not WorkerCleanup.pending(
            record
        ):
            return None
        return self._executor.submit(worker_id, lambda: self._cleanup(worker_id))

    def cleanup_idle(self) -> bool:
        """Check whether all scheduled cleanup has settled."""
        return self._executor.is_idle()

    def shutdown(self) -> None:
        """Stop accepting cleanup and discard work that has not started."""
        self._executor.shutdown()

    def observe(
        self,
        record: WorkerRecord,
        lifecycle: WorkerLifecycle,
    ) -> WorkerRecord:
        """Checkpoint confirmed death and enqueue cleanup without awaiting it."""
        try:
            observed, alive = lifecycle.observe(record)
        except (
            KeyError,
            OSError,
            ValueError,
            RuntimeError,
            subprocess.SubprocessError,
        ) as exc:
            logger.warning(
                "Worker %s reconciliation unavailable (%s)",
                record.id,
                type(exc).__name__,
            )
            return record
        if alive:
            self._inventory.confirm(record.id)
        elif observed.state == "exited" and record.state != "exited":
            self._inventory.close(record.id)
            try:
                self._inventory.save(observed)
            except (OSError, ValueError) as exc:
                logger.warning("Worker retirement not saved (%s)", type(exc).__name__)
            else:
                self._release(record.id)
                self.schedule(observed.id)
        return observed

    def _cleanup(self, worker_id: str) -> None:
        try:
            record = self._inventory.get(worker_id)
            if not WorkerCleanup.pending(record):
                return
            result = WorkerCleanup.run(record, self._profiles)
            self._inventory.update(
                record.id, lambda current: self._merge(current, result)
            )
        except (OSError, ValueError) as exc:
            logger.warning("Worker cleanup outcome not saved (%s)", type(exc).__name__)

    @staticmethod
    def _merge(record: WorkerRecord, result: WorkerRecord) -> WorkerRecord:
        """Fold cleanup markers only, preserving any late worker reports."""
        keys = ("cleanup_pending", "cleanup_error")
        return replace(
            record,
            last_report={
                **{k: v for k, v in record.last_report.items() if k not in keys},
                **{k: result.last_report[k] for k in keys if k in result.last_report},
            },
        )
