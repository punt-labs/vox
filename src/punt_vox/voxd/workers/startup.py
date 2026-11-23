"""Reconcile durable worker records before making them available to RPCs."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, final

from punt_vox.voxd.workers.lifecycle import WorkerLifecycle, WorkerLiveness
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
    ) -> tuple[list[WorkerRecord], set[str]]:
        """Restore active workers and reap orphans without harness cleanup."""

        def recover() -> tuple[list[WorkerRecord], set[str]]:
            registered = records.load(strict=False)
            active = [
                record
                for record in registered
                if record.provider == "tmux" and record.state in ("starting", "ready")
            ]
            status = dict(
                StartupProbes.completed(
                    active,
                    lambda record: (record.id, WorkerLifecycle.probe(record, provider)),
                )
            )
            reconciled = [
                cls._retire(record, records)
                if status.get(record.id) is WorkerLiveness.EXITED
                else record
                for record in registered
            ]
            WorkerRecovery.restore(reconciled, store)
            WorkerReaper.run(records, provider)
            confirmed_live = {
                worker_id
                for worker_id, liveness in status.items()
                if liveness is WorkerLiveness.ALIVE
            }
            return reconciled, {record.id for record in active} - confirmed_live

        return StartupProbes.run(recover)

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
