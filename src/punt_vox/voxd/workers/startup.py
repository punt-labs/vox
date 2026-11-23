"""Reconcile durable worker records before making them available to RPCs."""

from __future__ import annotations

import logging
import subprocess
from typing import TYPE_CHECKING, final

from punt_vox.voxd.workers.reaper import WorkerReaper
from punt_vox.voxd.workers.recovery import WorkerRecovery
from punt_vox.voxd.workers.startup_probes import StartupProbes

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

        def recover() -> list[WorkerRecord]:
            registered = records.load(strict=False)
            active = (
                record
                for record in registered
                if record.provider == "tmux" and record.state in ("starting", "ready")
            )
            dead = {
                worker_id
                for worker_id, exited in StartupProbes.completed(
                    active,
                    lambda record: (record.id, cls._confirmed_dead(record, provider)),
                )
                if exited
            }
            reconciled = [
                cls._retire(record, records) if record.id in dead else record
                for record in registered
            ]
            WorkerRecovery.restore(reconciled, store)
            WorkerReaper.run(records, provider)
            return reconciled

        return StartupProbes.run(recover)

    @staticmethod
    def _confirmed_dead(record: WorkerRecord, provider: TmuxWorkerProvider) -> bool:
        try:
            return not provider.status(record.session)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            logger.warning(
                "Worker %s startup reconciliation unavailable (%s)",
                record.id,
                type(exc).__name__,
            )
        return False

    @staticmethod
    def _retire(record: WorkerRecord, records: WorkerRecords) -> WorkerRecord:
        try:
            retired = record.retire()
            records.save(retired)
            return retired
        except (OSError, ValueError) as exc:
            logger.warning(
                "Worker %s startup retirement not saved (%s)",
                record.id,
                type(exc).__name__,
            )
        return record
