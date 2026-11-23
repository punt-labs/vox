"""Schedule trust cleanup independently of worker snapshots and hook delivery."""

from __future__ import annotations

import logging
import subprocess
from dataclasses import replace
from threading import Lock
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

    __slots__ = (
        "_executor",
        "_inventory",
        "_job_lock",
        "_jobs",
        "_profiles",
        "_release",
    )
    _jobs: set[str]
    _job_lock: Lock
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
        self._jobs = set()
        self._job_lock = Lock()
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
        with self._job_lock:
            record = self._inventory.get(worker_id)
            if worker_id in self._jobs or record.state not in ("exited", "stopped"):
                return None
            if not WorkerCleanup.pending(record):
                return None
            job = self._executor.submit(lambda: self._cleanup(record))
            if job.cancelled():
                return None
            self._jobs.add(worker_id)
            return job

    def shutdown(self) -> None:
        """Stop accepting cleanup and discard work that has not started."""
        self._executor.shutdown()
        with self._job_lock:
            self._jobs.clear()

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

    def _cleanup(self, record: WorkerRecord) -> None:
        try:
            result = WorkerCleanup.run(record, self._profiles)
            self._inventory.update(
                record.id, lambda current: self._merge(current, result)
            )
        except (OSError, ValueError) as exc:
            logger.warning("Worker cleanup outcome not saved (%s)", type(exc).__name__)
        finally:
            with self._job_lock:
                self._jobs.discard(record.id)

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
