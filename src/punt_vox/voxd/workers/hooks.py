"""Admit managed hooks promptly and serialize their durable progress checkpoints."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Self, final

from punt_vox.voxd.hook_context_store import HookContextStore
from punt_vox.voxd.wire_reply import WireReply
from punt_vox.voxd.workers.registry import WorkerRegistry

if TYPE_CHECKING:
    from starlette.websockets import WebSocket

__all__ = ["WorkerHooks"]


@final
class WorkerHooks:
    """Keep admission responsive while lifecycle and checkpoints share an actor."""

    __slots__ = ("_lock", "_registry", "_store")
    _store: HookContextStore
    _registry: WorkerRegistry
    _lock: asyncio.Lock

    def __new__(
        cls, store: HookContextStore, registry: WorkerRegistry, lock: asyncio.Lock
    ) -> Self:
        """Bind the same store and actor used by worker RPCs."""
        self = super().__new__(cls)
        self._store, self._registry, self._lock = store, registry, lock
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
            await asyncio.to_thread(self._ingest, payload, worker_only=worker_only)

    def _ingest(self, payload: dict[str, object], *, worker_only: bool) -> None:
        session, cwd = payload.get("session_id"), payload.get("cwd")
        if worker_only and not self._registry.admits_hook(session, cwd):
            return
        self._store.ingest(payload)
        self._registry.checkpoint_progress(session, cwd)
