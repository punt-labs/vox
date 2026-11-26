"""Wire handlers for the cache status and clear verbs.

Both route the daemon's *own* MP3 quip cache -- the directory the daemon reads
and writes during synthesis -- to a wire client, so ``vox cache status`` and
``vox cache clear`` operate on the daemon's cache rather than the caller's local
one. Each handler holds its cache
operation as an injected collaborator and replies through :class:`WireReply`, so
a filesystem fault -- a denied unlink, a stat error mid-scan -- is an
audit-logged operational fault (ERROR "operation failed"), never a silent no-op
or a torn connection.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Self, final

from punt_vox.voxd.data_root_boundary import relativize_to_data_root
from punt_vox.voxd.wire_fault import SafeFault
from punt_vox.voxd.wire_reply import WireReply

if TYPE_CHECKING:
    from collections.abc import Callable

    from starlette.websockets import WebSocket

    from punt_vox.cache import CacheInfo
    from punt_vox.voxd.cache_reply_types import CacheReply, CacheStatusReply

__all__ = ["CacheHandler"]


@final
class CacheHandler:
    """Run a cache operation in a worker and return its result on the wire."""

    __slots__ = ("_operation",)
    _operation: Callable[[], CacheReply]

    def __new__(cls, operation: Callable[[], CacheReply]) -> Self:
        """Bind the operation and its success payload."""
        self = super().__new__(cls)
        self._operation = operation
        return self

    @classmethod
    def status(cls, status: Callable[[], CacheInfo]) -> Self:
        """Report the daemon cache's entries, size, and safe relative path."""

        def payload() -> CacheStatusReply:
            info = status()
            # The bare name is the fail-closed fallback out of jail.
            rel = relativize_to_data_root(info.path)
            return {
                "type": "cache_status",
                "entries": info.entries,
                "size_bytes": info.size_bytes,
                "path": str(rel.path) if rel is not None else info.path.name,
            }

        return cls(payload)

    @classmethod
    def clear(cls, clear: Callable[[], int]) -> Self:
        """Delete the daemon cache and report the delete count."""
        return cls(lambda: {"type": "cache_cleared", "cleared": clear()})

    async def __call__(self, msg: dict[str, object], websocket: WebSocket) -> None:
        """Reply with the result, or report an audited operational fault."""
        reply = WireReply(websocket, str(msg.get("id", "")))
        try:
            payload = await asyncio.to_thread(self._operation)
        except OSError as exc:
            await reply.fault(await asyncio.to_thread(SafeFault.from_exception, exc))
            return
        await reply.send({**payload})
