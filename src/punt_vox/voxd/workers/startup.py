"""Reconcile durable worker records before making them available to RPCs."""

from __future__ import annotations

import logging
import subprocess
from typing import TYPE_CHECKING, final

from punt_vox.voxd.workers.reaper import WorkerReaper
from punt_vox.voxd.workers.recovery import WorkerRecovery

if TYPE_CHECKING:
    from punt_vox.voxd.hook_context_store import HookContextStore
    from punt_vox.voxd.workers.model import WorkerRecord
    from punt_vox.voxd.workers.records import WorkerRecords
    from punt_vox.voxd.workers.tmux import TmuxWorkerProvider

logger = logging.getLogger(__name__)


@final
class WorkerStartup:
    """Order liveness reconciliation, recovery and cleanup on daemon boot."""

    __slots__ = ()

    @classmethod
    def run(
        cls,
        records: WorkerRecords,
        store: HookContextStore,
        provider: TmuxWorkerProvider,
    ) -> list[WorkerRecord]:
        """Restore active workers and reap orphans without harness cleanup."""
        reconciled = [
            cls._reconcile(record, records, provider)
            for record in records.load(strict=False)
        ]
        WorkerRecovery.restore(reconciled, store)
        WorkerReaper.run(records, provider)
        return reconciled

    @staticmethod
    def _reconcile(
        record: WorkerRecord, records: WorkerRecords, provider: TmuxWorkerProvider
    ) -> WorkerRecord:
        if record.provider != "tmux" or record.state not in ("starting", "ready"):
            return record
        try:
            if not provider.status(record.session):
                retired = record.retire()
                records.save(retired)
                return retired
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            logger.warning(
                "Worker %s startup reconciliation unavailable (%s)",
                record.id,
                type(exc).__name__,
            )
        return record
