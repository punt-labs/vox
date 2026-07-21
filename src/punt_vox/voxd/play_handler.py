"""Play WebSocket handler: play a stored recording on the daemon host.

``vox play <id>`` routes here so audio comes out on the machine with speakers
(the daemon host), not on a remote client. The reference is a bare store name,
resolved and containment-checked exactly like a record name -- an absolute,
traversing, or separated ref is refused before any file is touched, and only a
file that exists inside the daemon-owned store is played. Playback runs through
the shared serialized :class:`PlaybackQueue`, so no audio is killed and the
flock ordering holds.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Self

from starlette.websockets import WebSocket, WebSocketDisconnect

from punt_vox.voxd._parse import parse_optional_str
from punt_vox.voxd.playback import PlaybackItem
from punt_vox.voxd.types import MessageHandler

if TYPE_CHECKING:
    from punt_vox.voxd.playback import PlaybackQueue
    from punt_vox.voxd.record_store import RecordStore

__all__ = ["PlayHandler"]

logger = logging.getLogger(__name__)


class PlayHandler(MessageHandler):
    """Handle 'play' messages: play a store recording on the daemon host."""

    __slots__ = ("_playback", "_store")

    _playback: PlaybackQueue
    _store: RecordStore

    def __new__(cls, *, playback: PlaybackQueue, store: RecordStore) -> Self:
        self = super().__new__(cls)
        self._playback = playback
        self._store = store
        return self

    async def __call__(self, msg: dict[str, object], websocket: WebSocket) -> None:
        """Resolve a store reference, enqueue it on the daemon, ack when queued."""
        request_id = str(msg.get("id", ""))
        ref = parse_optional_str(msg, "ref")
        if not ref:
            await self._error(websocket, request_id, "play requires a ref")
            return

        try:
            path = self._store.resolve_ref(ref)
        except ValueError as exc:
            await self._error(websocket, request_id, str(exc))
            return

        if not path.is_file():
            await self._error(websocket, request_id, f"no recording named {ref!r}")
            return

        logger.info("Play: id=%r ref=%r", request_id, ref)
        done_event = asyncio.Event()
        await self._playback.enqueue(
            PlaybackItem(path=path, request_id=f"play:{ref}", notify=done_event)
        )
        await websocket.send_json({"type": "playing", "id": request_id})
        await done_event.wait()
        with contextlib.suppress(WebSocketDisconnect, RuntimeError):
            await websocket.send_json({"type": "done", "id": request_id})

    @staticmethod
    async def _error(websocket: WebSocket, request_id: str, message: str) -> None:
        """Send an id-stamped error frame for a rejected play request."""
        await websocket.send_json(
            {"type": "error", "id": request_id, "message": message}
        )
