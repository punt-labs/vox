"""Record a failed launch's worktree discard so it runs off the actor lock."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, ClassVar, final

from punt_vox.voxd.workers.markers import ReportMarkers

if TYPE_CHECKING:
    from punt_vox.voxd.workers.model import WorkerRecord
    from punt_vox.voxd.workers.policy import WorkerRepoPolicy

logger = logging.getLogger(__name__)


@final
class WorkerDiscard:
    """Manage the pending and outcome markers of a worktree discard.

    The discard itself runs git and removes directories, so it is queued for
    the cleanup thread rather than run under the actor lock. A launch whose
    provider could not be stopped is not terminal: its pane may still be
    live, so no discard is queued and ``discard_skipped`` says why.
    """

    MARKERS: ClassVar[ReportMarkers] = ReportMarkers(
        "discard_pending", "discard_skipped", "discard_error", "worktree_discarded"
    )
    __slots__ = ()

    @staticmethod
    def queued(record: WorkerRecord) -> bool:
        """Check whether a discard is waiting for the cleanup thread."""
        return record.last_report.get("discard_pending") is True

    @classmethod
    def mark(cls, record: WorkerRecord) -> WorkerRecord:
        """Queue the discard of a stopped launch, or record why it is kept."""
        if record.state != "stopped":
            logger.warning("Worker %s worktree kept: provider stop failed", record.id)
            return cls.MARKERS.replace(
                record, {"discard_skipped": "provider stop failed"}
            )
        return cls.MARKERS.replace(record, {"discard_pending": True})

    @classmethod
    def run(cls, record: WorkerRecord, policy: WorkerRepoPolicy) -> WorkerRecord:
        """Discard the worktree and replace the pending marker with the outcome."""
        return policy.discard_recorded(cls.MARKERS.strip(record))
