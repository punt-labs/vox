"""Schedule trust cleanup independently of worker snapshots and hook delivery."""

from __future__ import annotations

import logging
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from typing import TYPE_CHECKING, Self, final

from punt_vox.voxd.workers.cleanup import WorkerCleanup

if TYPE_CHECKING:
    from concurrent.futures import Future

    from punt_vox.voxd.workers.contracts import HarnessProfile
    from punt_vox.voxd.workers.inventory import WorkerInventory
    from punt_vox.voxd.workers.lifecycle import WorkerLifecycle
    from punt_vox.voxd.workers.model import WorkerRecord

logger = logging.getLogger(__name__)


@final
class WorkerRetirement:
    """Run terminal cleanup once per worker until the record can be updated."""

    __slots__ = ("_executor", "_jobs", "_profiles")
    _jobs: dict[str, Future[WorkerRecord]]
    _executor: ThreadPoolExecutor
    _profiles: dict[str, HarnessProfile]

    def __new__(cls, profiles: dict[str, HarnessProfile]) -> Self:
        """Use one background writer for harness cleanup."""
        self = super().__new__(cls)
        self._profiles = profiles
        self._jobs = {}
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="vox-cleanup"
        )
        return self

    def schedule(self, record: WorkerRecord) -> None:
        """Queue a confirmed exit without waiting for a config file lease."""
        if (
            record.state == "exited"
            and WorkerCleanup.pending(record)
            and record.id not in self._jobs
        ):
            self._jobs[record.id] = self._executor.submit(
                WorkerCleanup.run, record, self._profiles
            )

    def observe(
        self,
        record: WorkerRecord,
        lifecycle: WorkerLifecycle,
        inventory: WorkerInventory,
    ) -> WorkerRecord:
        """Checkpoint confirmed death and enqueue cleanup without awaiting it."""
        try:
            observed, alive = lifecycle.observe(self.status(record))
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
            inventory.confirm(record.id)
        elif observed.state == "exited":
            inventory.close(record.id)
            try:
                inventory.save(observed)
            except (OSError, ValueError) as exc:
                logger.warning("Worker retirement not saved (%s)", type(exc).__name__)
            self.schedule(observed)
        return observed

    def status(self, record: WorkerRecord) -> WorkerRecord:
        """Fold finished cleanup without blocking a snapshot."""
        job = self._jobs.get(record.id)
        return self.finish(record) if job is not None and job.done() else record

    def queued(self, worker_id: str) -> bool:
        """Check whether cleanup is already queued or running."""
        job = self._jobs.get(worker_id)
        return job is not None and not job.done()

    def finish(self, record: WorkerRecord) -> WorkerRecord:
        """Fold cleanup markers only, preserving any late worker reports."""
        job = self._jobs.pop(record.id, None)
        if job is None:
            return record
        result = job.result()
        keys = ("cleanup_pending", "cleanup_error")
        return replace(
            record,
            last_report={
                **{k: v for k, v in record.last_report.items() if k not in keys},
                **{k: result.last_report[k] for k in keys if k in result.last_report},
            },
        )
