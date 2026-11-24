"""Everything a terminal worker still owes: harness cleanup and a discard."""

from __future__ import annotations

from typing import TYPE_CHECKING, Self, final

from punt_vox.voxd.workers.cleanup import WorkerCleanup
from punt_vox.voxd.workers.discard import WorkerDiscard

if TYPE_CHECKING:
    from punt_vox.voxd.workers.contracts import HarnessProfile
    from punt_vox.voxd.workers.markers import ReportMarkers
    from punt_vox.voxd.workers.model import WorkerRecord
    from punt_vox.voxd.workers.policy import WorkerRepoPolicy


@final
class SettledDebts:
    """The outcome of one settlement job: the record it produced and the
    marker sets of the debts it actually ran.

    Folding back only those sets keeps a debt marked pending by a concurrent
    stop, during the job, from being overwritten by the job's stale snapshot.
    """

    __slots__ = ("_markers", "_result")
    _markers: tuple[ReportMarkers, ...]
    _result: WorkerRecord

    def __new__(cls, result: WorkerRecord, markers: tuple[ReportMarkers, ...]) -> Self:
        """Bind the settled record to the marker sets it is authoritative for."""
        self = super().__new__(cls)
        self._result = result
        self._markers = markers
        return self

    @property
    def result(self) -> WorkerRecord:
        """The record as the debts left it."""
        return self._result

    def fold(self, record: WorkerRecord) -> WorkerRecord:
        """Copy only the markers of the debts that ran onto a newer *record*."""
        for markers in self._markers:
            record = markers.fold(record, self._result)
        return record

    def left_owing(self, record: WorkerRecord) -> bool:
        """Check whether *record* owes a debt this job did not run.

        A stop during the job marks such a debt pending; a debt that ran and
        failed is not owed again until the next stop.
        """
        return (
            WorkerCleanup.MARKERS not in self._markers and WorkerCleanup.pending(record)
        ) or (
            WorkerDiscard.MARKERS not in self._markers and WorkerDiscard.queued(record)
        )


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

    def settle(self, record: WorkerRecord) -> SettledDebts:
        """Run whichever of harness cleanup and worktree discard is owed."""
        ran: list[ReportMarkers] = []
        if WorkerCleanup.pending(record):
            record = WorkerCleanup.run(record, self._profiles)
            ran.append(WorkerCleanup.MARKERS)
        if WorkerDiscard.queued(record):
            record = WorkerDiscard.run(record, self._policy)
            ran.append(WorkerDiscard.MARKERS)
        return SettledDebts(record, tuple(ran))
