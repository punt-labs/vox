"""Everything a terminal worker still owes: harness cleanup and a discard."""

from __future__ import annotations

from typing import TYPE_CHECKING, Self, final

from punt_vox.voxd.workers.cleanup import WorkerCleanup
from punt_vox.voxd.workers.discard import WorkerDiscard

if TYPE_CHECKING:
    from punt_vox.voxd.workers.contracts import HarnessProfile
    from punt_vox.voxd.workers.model import WorkerRecord
    from punt_vox.voxd.workers.policy import WorkerRepoPolicy


@final
class WorkerSettlement:
    """Run the debts one queued job settles, and fold only their markers back.

    Harness cleanup removes temporary trust state; the discard removes a
    failed launch's worktree. Both run git or the filesystem, so both belong
    on the cleanup thread, never under the actor lock.
    """

    __slots__ = ("_policy", "_profiles")
    _policy: WorkerRepoPolicy
    _profiles: dict[str, HarnessProfile]

    def __new__(
        cls, profiles: dict[str, HarnessProfile], policy: WorkerRepoPolicy
    ) -> Self:
        """Bind the harness catalog and the repository policy the debts need."""
        self = super().__new__(cls)
        self._profiles = profiles
        self._policy = policy
        return self

    @staticmethod
    def owed(record: WorkerRecord) -> bool:
        """Check whether the record still carries a debt to settle."""
        return WorkerCleanup.pending(record) or WorkerDiscard.queued(record)

    def settle(self, record: WorkerRecord) -> WorkerRecord:
        """Run whichever of harness cleanup and worktree discard is owed."""
        if WorkerCleanup.pending(record):
            record = WorkerCleanup.run(record, self._profiles)
        if WorkerDiscard.queued(record):
            record = WorkerDiscard.run(record, self._policy)
        return record

    @staticmethod
    def fold(record: WorkerRecord, result: WorkerRecord) -> WorkerRecord:
        """Copy only the settled markers onto a possibly newer record."""
        folded = WorkerCleanup.MARKERS.fold(record, result)
        return WorkerDiscard.MARKERS.fold(folded, result)
