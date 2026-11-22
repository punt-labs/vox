"""WebSocket endpoints for hook ingestion and curated session context."""

from __future__ import annotations

from typing import TYPE_CHECKING, Self, cast, final

from punt_vox.voxd._parse import parse_required_str
from punt_vox.voxd.hook_context_store import HookContextStore
from punt_vox.voxd.wire_reply import WireReply

if TYPE_CHECKING:
    from starlette.websockets import WebSocket

    from punt_vox.voxd.hook_context_types import HookIngest
    from punt_vox.voxd.types import MessageHandler


@final
class HookContextHandlers:
    """Bind the two RPCs to the same daemon-owned context store."""

    __slots__ = ("_ingest", "_store")
    _store: HookContextStore
    _ingest: HookIngest | None  # PY-TS-14: standalone stores have no worker actor.

    def __new__(
        cls,
        store: HookContextStore | None = None,  # PY-TS-14: absent creates a store.
        ingest: HookIngest | None = None,  # PY-TS-14: absent uses unmanaged ingestion.
    ) -> Self:
        """Create a fresh store for this daemon."""
        self = super().__new__(cls)
        self._store = store if store is not None else HookContextStore()
        self._ingest = ingest
        return self

    def handlers(self) -> dict[str, MessageHandler]:
        """Expose the RPC dispatch entries."""
        return {"hook_event": self.ingest, "hook_context": self.snapshot}

    async def ingest(self, msg: dict[str, object], websocket: WebSocket) -> None:
        """Accept exactly one live event; never accept a ledger or snapshot."""
        reply = WireReply(websocket, str(msg.get("id", "")))
        payload = msg.get("payload")
        if not isinstance(payload, dict):
            await reply.error("hook_event requires one hook payload object")
            return
        try:
            worker_only = msg.get("worker_only", False)
            if not isinstance(worker_only, bool):
                raise ValueError("worker_only must be boolean")
            event = cast("dict[str, object]", payload)
            if self._ingest is not None:
                await self._ingest(event, worker_only=worker_only)
            elif worker_only:
                raise ValueError("Worker admission is unavailable")
            else:
                self._store.ingest(event)
        except RecursionError:
            await reply.error("hook payload nesting exceeds the limit")
            return
        except (ValueError, OSError, RuntimeError) as exc:
            await reply.error(str(exc))
            return
        await reply.send({"type": "hook_event_accepted"})

    async def snapshot(self, msg: dict[str, object], websocket: WebSocket) -> None:
        """Return a snapshot for just the requested hook session."""
        reply = WireReply(websocket, str(msg.get("id", "")))
        try:
            snapshot = self._store.snapshot(parse_required_str(msg, "session_id"))
        except ValueError as exc:
            await reply.error(exc)
            return
        await reply.send({"type": "hook_context", "snapshot": snapshot})
