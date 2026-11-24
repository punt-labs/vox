"""Schedule trust cleanup independently of worker snapshots and hook delivery."""

from __future__ import annotations

import logging
import subprocess
from typing import TYPE_CHECKING, Self, final

from punt_vox.voxd.workers.cleanup_queue import WorkerCleanupQueue
from punt_vox.voxd.workers.settlement import WorkerSettlement

if TYPE_CHECKING:
    from collections.abc import Callable
    from concurrent.futures import Future

    from punt_vox.voxd.workers.contracts import HarnessProfile
    from punt_vox.voxd.workers.inventory import WorkerInventory
    from punt_vox.voxd.workers.lifecycle import WorkerLifecycle
    from punt_vox.voxd.workers.model import WorkerRecord
    from punt_vox.voxd.workers.policy import WorkerRepoPolicy

logger = logging.getLogger(__name__)


@final
class WorkerRetirement:
    """Run terminal cleanup once per worker and checkpoint its outcome."""

    __slots__ = ("_executor", "_inventory", "_release", "_settlement")
    _executor: WorkerCleanupQueue
    _inventory: WorkerInventory
    _release: Callable[[str], None]
    _settlement: WorkerSettlement

    def __new__(cls, settlement: WorkerSettlement, inventory: WorkerInventory) -> Self:
        """Use one background writer for harness cleanup and worktree discard."""
        self = super().__new__(cls)
        self._settlement = settlement
        self._inventory = inventory
        self._executor = WorkerCleanupQueue()
        self._release = lambda _worker: None
        return self

    @classmethod
    def for_registry(
        cls,
        profiles: dict[str, HarnessProfile],
        policy: WorkerRepoPolicy,
        inventory: WorkerInventory,
    ) -> Self:
        """Build the retirement over the debts the registry's catalogs settle."""
        return cls(WorkerSettlement(profiles, policy), inventory)

    def release_with(self, release: Callable[[str], None]) -> None:
        """Bind the actor-owned retention transition, not a cleanup job."""
        self._release = release

    def schedule(
        self, worker_id: str
    ) -> Future[None] | None:  # PY-TS-14: no future without a newly queued job.
        """Queue terminal cleanup without waiting for a config file lease."""
        record = self._inventory.get(worker_id)
        if record.state not in ("exited", "stopped") or not self._settlement.owed(
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
            self._retire(observed)
        return observed

    def _retire(self, observed: WorkerRecord) -> None:
        """Checkpoint a newly confirmed exit, then release it and queue cleanup."""
        self._inventory.close(observed.id)
        try:
            self._inventory.save(observed)
        except (OSError, ValueError) as exc:
            logger.warning("Worker retirement not saved (%s)", type(exc).__name__)
            return
        self._release(observed.id)
        self.schedule(observed.id)

    def _cleanup(self, worker_id: str) -> None:
        """Settle the owed debts, and any a stop queued while they ran.

        The queue holds one job per worker, so a stop that marks a debt
        pending during this job cannot queue its own; the job settles it
        before it ends. Only a debt this job did not run repeats the loop,
        never one it ran and recorded as failed.
        """
        try:
            record = self._inventory.get(worker_id)
            if not self._settlement.owed(record):
                return
            while True:
                settled = self._settlement.settle(record)
                record = self._inventory.update(record.id, settled.fold)
                if not settled.left_owing(record):
                    return
        except (OSError, ValueError) as exc:
            logger.warning("Worker cleanup outcome not saved (%s)", type(exc).__name__)
