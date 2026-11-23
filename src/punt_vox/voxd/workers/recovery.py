"""Restore durable worker hook projections before the daemon accepts hooks."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, cast, final

if TYPE_CHECKING:
    from punt_vox.voxd.hook_context_store import HookContextStore
    from punt_vox.voxd.hook_snapshot import WorkerHookCheckpoint
    from punt_vox.voxd.workers.records import WorkerRecords

logger = logging.getLogger(__name__)


@final
class WorkerRecovery:
    """Use durable records without replaying hooks or performing harness cleanup."""

    __slots__ = ()

    @staticmethod
    def restore(records: WorkerRecords, store: HookContextStore) -> None:
        """Restore each supported active checkpoint in isolation."""
        for record in records.load(strict=False):
            if (
                record.state not in ("starting", "ready")
                or "worker_seq" not in record.last_report
            ):
                continue
            try:
                store.restore(
                    record.id, cast("WorkerHookCheckpoint", record.last_report)
                )
            except (KeyError, TypeError, ValueError) as exc:
                logger.warning(
                    "Worker %s hook checkpoint unavailable (%s)",
                    record.id,
                    type(exc).__name__,
                )
