"""WebSocket endpoints for hook ingestion and curated session context."""

from __future__ import annotations

from typing import TYPE_CHECKING, Self, cast

from punt_vox.voxd._parse import parse_required_str
from punt_vox.voxd.hook_context_store import HookContextStore
from punt_vox.voxd.wire_reply import WireReply

if TYPE_CHECKING:
    from starlette.websockets import WebSocket

    from punt_vox.voxd.types import MessageHandler


class HookContextHandlers:
    """Bind the two RPCs to the same daemon-owned context store."""

    __slots__ = ("_store",)
    _store: HookContextStore

    def __new__(cls) -> Self:
        """Create a fresh store for this daemon."""
        self = super().__new__(cls)
        self._store = HookContextStore()
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
            self._store.ingest(cast("dict[str, object]", payload))
        except ValueError as exc:
            await reply.error(exc)
            return
        except RecursionError:
            await reply.error("hook payload nesting exceeds the limit")
            return
        await reply.send({"type": "hook_event_accepted"})

    async def snapshot(self, msg: dict[str, object], websocket: WebSocket) -> None:
        """Return a snapshot for just the requested Claude session."""
        reply = WireReply(websocket, str(msg.get("id", "")))
        try:
            snapshot = self._store.snapshot(parse_required_str(msg, "session_id"))
        except ValueError as exc:
            await reply.error(exc)
            return
        await reply.send({"type": "hook_context", "snapshot": snapshot})
