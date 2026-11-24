"""Record retryable harness cleanup independently of provider liveness."""

from __future__ import annotations

import logging
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, final

from punt_vox.voxd.workers.markers import ReportMarkers

if TYPE_CHECKING:
    from punt_vox.voxd.workers.contracts import HarnessProfile
    from punt_vox.voxd.workers.model import WorkerRecord

logger = logging.getLogger(__name__)


@final
class WorkerCleanup:
    """Manage pending and failed cleanup markers for a terminal worktree."""

    MARKERS: ClassVar[ReportMarkers] = ReportMarkers("cleanup_pending", "cleanup_error")
    __slots__ = ()

    @classmethod
    def pending(cls, record: WorkerRecord) -> bool:
        """Check whether explicit stop still owes harness cleanup."""
        return cls.MARKERS.present(record)

    @classmethod
    def run(
        cls, record: WorkerRecord, profiles: dict[str, HarnessProfile]
    ) -> WorkerRecord:
        """Remove temporary harness state while keeping the worktree."""
        try:
            profiles[record.harness].cleanup(Path(record.worktree))
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
        return cls.MARKERS.strip(record)
