"""Restore durable worker hook projections before the daemon accepts hooks."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, cast, final

if TYPE_CHECKING:
    from punt_vox.voxd.hook_context_store import HookContextStore
    from punt_vox.voxd.hook_snapshot import WorkerHookCheckpoint
    from punt_vox.voxd.workers.model import WorkerRecord

logger = logging.getLogger(__name__)


@final
class WorkerRecovery:
    """Use durable records without replaying hooks or performing harness cleanup."""

    __slots__ = ()

    @staticmethod
    def restore(records: list[WorkerRecord], store: HookContextStore) -> None:
        """Restore each supported active checkpoint in isolation."""
        for record in records:
            if record.state not in ("starting", "ready"):
                continue
            try:
                WorkerRecovery.restore_checkpoint(record, store)
            except (KeyError, TypeError, ValueError) as exc:
                logger.warning(
                    "Worker %s hook checkpoint unavailable (%s)",
                    record.id,
                    type(exc).__name__,
                )

    @staticmethod
    def restore_checkpoint(record: WorkerRecord, store: HookContextStore) -> None:
        """Load an existing projection; malformed checkpoints are not overwritten."""
        if "worker_seq" in record.last_report:
            store.restore(record.id, cast("WorkerHookCheckpoint", record.last_report))
        elif record.last_report.get("last_assistant_message"):
            raise ValueError("Worker result has no checkpoint ordering")
