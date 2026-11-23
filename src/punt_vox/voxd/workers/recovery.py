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
                checkpoint = WorkerRecovery.checkpoint(record)
                if checkpoint is not None:
                    store.restore(record.id, checkpoint)
            except (KeyError, TypeError, ValueError) as exc:
                logger.warning(
                    "Worker %s hook checkpoint unavailable (%s)",
                    record.id,
                    type(exc).__name__,
                )

    @staticmethod
    def checkpoint(record: WorkerRecord) -> WorkerHookCheckpoint | None:
        """Find the durable projection, refusing results with no ordering metadata."""
        if "worker_seq" in record.last_report:
            return cast("WorkerHookCheckpoint", record.last_report)
        if record.last_report.get("last_assistant_message"):
            raise ValueError("Worker result has no checkpoint ordering")
        return None
