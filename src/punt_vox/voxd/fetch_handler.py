"""Fetch WebSocket handler: return a stored recording's bytes to a remote client.

``vox fetch <id> -o <path>`` materializes a store recording on a client that
does not share the daemon's filesystem. The reference is a bare store name,
resolved and containment-checked exactly like a record name -- no client path,
no escape. The bytes are returned base64-encoded in a **single frame**, so a
recording larger than the frame budget is refused with a clear error rather than
silently truncated. Remote fetch of a large recording is out of scope for this
cut (the same limit that already made remote record above ~1 MiB non-functional);
a chunked streaming transport is a separate, formally-modelled follow-up.
"""

from __future__ import annotations

import base64
import logging
from typing import TYPE_CHECKING, Self

from punt_vox.types_audio import FETCH_FRAME_LIMIT_BYTES
from punt_vox.voxd._parse import parse_optional_str
from punt_vox.voxd.types import MessageHandler

if TYPE_CHECKING:
    from starlette.websockets import WebSocket

    from punt_vox.voxd.record_store import RecordStore

__all__ = ["FetchHandler"]

logger = logging.getLogger(__name__)


class FetchHandler(MessageHandler):
    """Handle 'fetch' messages: return a store recording's bytes in one frame."""

    __slots__ = ("_store",)

    _store: RecordStore

    def __new__(cls, *, store: RecordStore) -> Self:
        self = super().__new__(cls)
        self._store = store
        return self

    async def __call__(self, msg: dict[str, object], websocket: WebSocket) -> None:
        """Resolve a store reference and return its bytes, or an error frame."""
        request_id = str(msg.get("id", ""))
        ref = parse_optional_str(msg, "ref")
        if not ref:
            await self._error(websocket, request_id, "fetch requires a ref")
            return

        try:
            path = self._store.resolve_ref(ref)
        except ValueError as exc:
            await self._error(websocket, request_id, str(exc))
            return

        if not path.is_file():
            await self._error(websocket, request_id, f"no recording named {ref!r}")
            return

        # The filesystem reads (stat, read_bytes) can fail on a race -- the file
        # deleted between is_file() and here -- or a permission/IO fault. Any
        # such OSError must reach the client as a clean error frame, never a
        # server-side traceback (matching the record write path).
        try:
            # A cheap pre-read stat rejects the common oversize case without
            # reading a huge file into memory. It is NOT authoritative: a
            # concurrent record place() can replace/grow the file between stat
            # and read, so the reply's size and the limit are re-checked below
            # against the bytes actually read.
            if path.stat().st_size > FETCH_FRAME_LIMIT_BYTES:
                await self._reject_oversize(websocket, request_id, path.stat().st_size)
                return
            raw = path.read_bytes()
        except OSError as exc:
            logger.warning(
                "Fetch read failed for id=%r ref=%r: %s", request_id, ref, exc
            )
            await self._error(
                websocket, request_id, f"cannot read recording {ref!r}: {exc}"
            )
            return

        # Authoritative size = what we read. Guards the stat-vs-read race: a
        # file grown past the limit after the stat is rejected here, and the
        # client's byte-count check is compared against a declaration that
        # matches the payload, never a stale stat.
        size = len(raw)
        if size > FETCH_FRAME_LIMIT_BYTES:
            await self._reject_oversize(websocket, request_id, size)
            return

        logger.info("Fetch: id=%r ref=%r bytes=%d", request_id, ref, size)
        data = base64.b64encode(raw).decode("ascii")
        await websocket.send_json(
            {
                "type": "bytes",
                "id": request_id,
                "ref": path.name,
                "data": data,
                "bytes": size,
            }
        )

    @staticmethod
    async def _reject_oversize(
        websocket: WebSocket, request_id: str, size: int
    ) -> None:
        """Send the too-large-to-fetch error frame for a *size*-byte recording."""
        await websocket.send_json(
            {
                "type": "error",
                "id": request_id,
                "message": (
                    f"recording too large to fetch in one frame ({size} bytes > "
                    f"{FETCH_FRAME_LIMIT_BYTES}); retrieve it from the host directly"
                ),
            }
        )

    @staticmethod
    async def _error(websocket: WebSocket, request_id: str, message: str) -> None:
        """Send an id-stamped error frame for a rejected fetch request."""
        await websocket.send_json(
            {"type": "error", "id": request_id, "message": message}
        )
