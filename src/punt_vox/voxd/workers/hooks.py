"""Admit managed hooks promptly and serialize their durable progress checkpoints."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Self, final

from punt_vox.voxd.wire_reply import WireReply
from punt_vox.voxd.workers.hook_reports import HookReports
from punt_vox.voxd.workers.recovery import WorkerRecovery
from punt_vox.voxd.workers.registry import WorkerRegistry

if TYPE_CHECKING:
    from starlette.websockets import WebSocket

    from punt_vox.voxd.hook_context_store import HookContextStore
    from punt_vox.voxd.voice_call.progress import ProgressEvents
    from punt_vox.voxd.voice_call.progress_types import WorkerProgress
    from punt_vox.voxd.workers.model import WorkerRecord

__all__ = ["WorkerHooks"]


@final
class WorkerHooks:
    """Keep admission responsive while lifecycle and checkpoints share an actor."""

    __slots__ = ("_events", "_lock", "_registry", "_reports", "_store")
    _reports: HookReports
    _events: ProgressEvents | None  # None drops events when no call stream is wired.
    _store: HookContextStore
    _registry: WorkerRegistry
    _lock: asyncio.Lock

    def __new__(
        cls,
        store: HookContextStore,
        registry: WorkerRegistry,
        lock: asyncio.Lock,
        events: ProgressEvents | None = None,  # None omits call subscribers.
    ) -> Self:
        """Bind the same store and actor used by worker RPCs."""
        self = super().__new__(cls)
        self._store, self._registry, self._lock = store, registry, lock
        self._reports = HookReports()
        self._events = events
        return self

    async def admit(self, msg: dict[str, object], websocket: WebSocket) -> None:
        """Check advisory metadata without waiting behind worker launches."""
        reply = WireReply(websocket, str(msg.get("id", "")))
        try:
            admitted = await asyncio.to_thread(self._target, msg) is not None
        except (OSError, ValueError, RuntimeError) as exc:
            await reply.error(str(exc))
            return
        await reply.send({"type": "worker_hook_admission", "admitted": admitted})

    async def ingest(self, payload: dict[str, object], *, worker_only: bool) -> None:
        """Revalidate admission and checkpoint before returning a delivery receipt."""
        if not worker_only and await asyncio.to_thread(self._target, payload) is None:
            self._store.ingest(payload)
            return
        async with self._lock:
            event = await asyncio.to_thread(
                self._ingest, payload, worker_only=worker_only
            )
            if event is not None and self._events is not None:
                self._events.publish(event)

    def _ingest(
        self, payload: dict[str, object], *, worker_only: bool
    ) -> WorkerProgress | None:
        record = self._target(payload)
        if record is None:
            if not worker_only:
                self._store.ingest(payload)
            return None
        sequence = payload.get("relay_seq")
        if type(sequence) is int and self._registry.hook_records.received(
            record, sequence
        ):
            return None
        self._project(record, payload)
        snapshot = self._store.snapshot(record.id)
        return self._reports.observe(
            record.id,
            payload,
            snapshot["pending_decision"],
            latest=self._store.worker_sequence(record.id),
        )

    def _target(self, msg: dict[str, object]) -> WorkerRecord | None:
        return self._registry.hook_records.match(
            msg.get("session_id"), msg.get("cwd"), msg.get("hook_event_name")
        )

    def _project(self, record: WorkerRecord, payload: dict[str, object]) -> None:
        exited = self._registry.hook_records.exited(record)
        if exited:
            self._restore_exit(record)
        try:
            self._store.ingest(payload, worker_input=True)
            if exited:
                checkpoint = self._store.checkpoint(record.id)
                if checkpoint is not None:
                    self._registry.checkpoint_exit(record, checkpoint)
            else:
                self._registry.checkpoint_progress(record)
        finally:
            if exited:
                self._store.release_worker(record.id)

    def _restore_exit(self, record: WorkerRecord) -> None:
        try:
            self._store.restore(record.id, WorkerRecovery.checkpoint(record))
        except (KeyError, TypeError, ValueError):
            raise ValueError("Saved worker hook checkpoint is unavailable") from None
