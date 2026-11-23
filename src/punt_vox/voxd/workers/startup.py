"""Reconcile durable worker records before making them available to RPCs."""

from __future__ import annotations

import logging
import subprocess
from dataclasses import replace
from typing import TYPE_CHECKING, final

from punt_vox.voxd.workers.reaper import WorkerReaper
from punt_vox.voxd.workers.recovery import WorkerRecovery

if TYPE_CHECKING:
    from punt_vox.voxd.hook_context_store import HookContextStore
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
    ) -> None:
        """Restore active workers and reap orphans without harness cleanup."""
        cls._reconcile(records, provider)
        WorkerRecovery.restore(records, store)
        WorkerReaper.run(records, provider)

    @staticmethod
    def _reconcile(records: WorkerRecords, provider: TmuxWorkerProvider) -> None:
        for record in records.load(strict=False):
            if record.provider != "tmux" or record.state not in ("starting", "ready"):
                continue
            try:
                if not provider.status(record.session):
                    records.save(replace(record, state="exited"))
            except (
                OSError,
                ValueError,
                RuntimeError,
                subprocess.SubprocessError,
            ) as exc:
                logger.warning(
                    "Worker %s startup reconciliation unavailable (%s)",
                    record.id,
                    type(exc).__name__,
                )
