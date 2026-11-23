"""Admit managed hooks promptly and serialize their durable progress checkpoints."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Self, final

from punt_vox.voxd.hook_context_store import HookContextStore
from punt_vox.voxd.wire_reply import WireReply
from punt_vox.voxd.workers.hook_reports import HookReports
from punt_vox.voxd.workers.registry import WorkerRegistry

if TYPE_CHECKING:
    from starlette.websockets import WebSocket

    from punt_vox.voxd.voice_call.progress import ProgressEvents
    from punt_vox.voxd.voice_call.progress_types import WorkerProgress

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
            admitted = await asyncio.to_thread(
                self._registry.admits_hook, msg.get("session_id"), msg.get("cwd")
            )
        except (OSError, ValueError, RuntimeError) as exc:
            await reply.error(str(exc))
            return
        await reply.send({"type": "worker_hook_admission", "admitted": admitted})

    async def ingest(self, payload: dict[str, object], *, worker_only: bool) -> None:
        """Revalidate admission and checkpoint before returning a delivery receipt."""
        async with self._lock:
            event = await asyncio.to_thread(
                self._ingest, payload, worker_only=worker_only
            )
            if event is not None and self._events is not None:
                self._events.publish(event)

    def _ingest(
        self, payload: dict[str, object], *, worker_only: bool
    ) -> WorkerProgress | None:
        session, cwd = payload.get("session_id"), payload.get("cwd")
        admitted = self._registry.admits_hook(session, cwd)
        if worker_only and not admitted:
            return None
        self._store.ingest(payload, worker_input=admitted)
        self._registry.checkpoint_progress(session, cwd)
        if not admitted:
            return None
        snapshot = self._store.snapshot(str(session))
        return self._reports.observe(
            str(session),
            payload,
            snapshot["pending_decision"],
            latest=snapshot["gaps"]["highest_seen"],
        )
