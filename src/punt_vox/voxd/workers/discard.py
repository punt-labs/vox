"""Record a failed launch's worktree discard so it runs off the actor lock."""

from __future__ import annotations

import logging
import subprocess
from typing import TYPE_CHECKING, ClassVar, final

from punt_vox.voxd.workers.git_failure import GitFailure
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
    live, so no discard is queued and ``discard_skipped`` says why. A skipped
    or failed discard is owed again on the next stop that leaves the worker
    stopped; only ``worktree_discarded`` settles it.
    """

    MARKERS: ClassVar[ReportMarkers] = ReportMarkers(
        "discard_pending", "discard_skipped", "discard_error", "worktree_discarded"
    )
    _UNSETTLED: ClassVar[ReportMarkers] = ReportMarkers(
        "discard_skipped", "discard_error"
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
        return cls._queue(record)

    @classmethod
    def retry(cls, record: WorkerRecord) -> WorkerRecord:
        """Queue a skipped or failed discard again once the worker is terminal.

        Terminal means the provider is confirmed gone: an explicit stop left
        the worker ``stopped``, or startup observed its pane dead and retired
        it to ``exited``. A rollback that could not stop the provider leaves
        ``discard_skipped``; without this a later retire would strand the
        worktree, since nothing else converts the marker into owed work.
        """
        if record.state in ("stopped", "exited") and cls._UNSETTLED.present(record):
            return cls._queue(record)
        return record

    @staticmethod
    def retire(record: WorkerRecord) -> WorkerRecord:
        """Record a confirmed exit; the discard stays owed until the stop lands.

        A dead pane says the worker's own process is gone, not that the
        session was torn down: the retired record carries ``pane_stop_pending``
        and the next terminate runs that stop first, then queues a skipped or
        failed discard through ``retry``. Queueing it here would delete the
        checkout before the stop was confirmed.
        """
        return record.retire()

    @classmethod
    def _queue(cls, record: WorkerRecord) -> WorkerRecord:
        """Replace every discard marker with the one that says it is owed."""
        return cls.MARKERS.replace(record, {"discard_pending": True})

    @classmethod
    def run(cls, record: WorkerRecord, policy: WorkerRepoPolicy) -> WorkerRecord:
        """Discard the worktree and replace the pending marker with the outcome.

        The policy records a git or path failure itself. An OSError,
        ValueError, RuntimeError or SubprocessError that escapes it still
        lands on the record as ``discard_error``, with its traceback in the
        log: the same settlement job may have run harness cleanup, and an
        exception here would lose that outcome with it. A programming error
        (TypeError, KeyError) escapes by design and fails the job.
        """
        try:
            return policy.discard_recorded(cls.MARKERS.strip(record))
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            failure = GitFailure(exc)
            logger.warning(
                "Worker %s worktree discard failed: %s",
                record.id,
                failure,
                exc_info=True,
            )
            return cls.MARKERS.replace(record, {"discard_error": str(failure)})
