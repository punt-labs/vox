"""Sequential message dispatch for one accepted WebSocket."""

from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from typing import TYPE_CHECKING, Self, cast, final

from starlette.websockets import WebSocketDisconnect, WebSocketState

if TYPE_CHECKING:
    from collections.abc import Mapping

    from starlette.websockets import WebSocket

    from punt_vox.voxd.types import MessageHandler

_MAX_READ_AHEAD = 32
logger = logging.getLogger(__name__)


@final
class WebSocketSession:
    """Dispatch one handler at a time, watching for close during streams."""

    __slots__ = ("_frames", "_handlers", "_pending", "_socket")

    _frames: deque[str]
    _handlers: Mapping[str, MessageHandler]
    _pending: asyncio.Task[str] | None
    _socket: WebSocket

    def __new__(cls, socket: WebSocket, handlers: Mapping[str, MessageHandler]) -> Self:
        """Bind an accepted socket to its dispatch table."""
        self = super().__new__(cls)
        self._frames = deque()
        self._handlers = handlers
        self._pending = None
        self._socket = socket
        return self

    async def serve(self) -> None:
        """Dispatch frames in order until the connection closes."""
        try:
            while self._socket.application_state == WebSocketState.CONNECTED:
                await self._dispatch(await self._receive())
        finally:
            if self._pending is not None:
                self._pending.cancel()
                await asyncio.gather(self._pending, return_exceptions=True)

    async def _receive(self) -> str:
        if self._frames:
            return self._frames.popleft()
        pending, self._pending = self._pending, None
        return await (self._socket.receive_text() if pending is None else pending)

    async def _dispatch(self, raw: str) -> None:
        try:
            decoded: object = json.loads(raw)
        except json.JSONDecodeError:
            await self._error("invalid JSON")
            return
        if not isinstance(decoded, dict):
            await self._error("expected JSON object")
            return
        msg = cast("dict[str, object]", decoded)
        kind = str(msg.get("type", ""))
        handler = self._handlers.get(kind)
        if handler is None:
            await self._error(f"unknown message type: {kind}", str(msg.get("id", "")))
        elif kind in {"fetch", "program_list", "rec_list"}:
            await self._stream(handler, msg)
        else:
            await handler(msg, self._socket)

    async def _stream(self, handler: MessageHandler, msg: dict[str, object]) -> None:
        stream = asyncio.create_task(handler(msg, self._socket))
        try:
            await self._watch_stream(stream)
        except BaseException:
            stream.cancel()
            (e,) = await asyncio.gather(stream, return_exceptions=True)
            if isinstance(e, Exception) and not isinstance(e, WebSocketDisconnect):
                logger.error("%s teardown failed", str(msg["type"]).title(), exc_info=e)
            raise
        await stream

    async def _watch_stream(self, stream: asyncio.Task[None]) -> None:
        # ASGI delivers disconnect through receive, in FIFO order. Buffer while
        # watching the stream, but retain backpressure for a saturated pipeline.
        while len(self._frames) < _MAX_READ_AHEAD:
            pending = self._pending
            if pending is None:
                pending = self._pending = asyncio.create_task(
                    self._socket.receive_text()
                )
            done, _ = await asyncio.wait(
                (pending, stream), return_when=asyncio.FIRST_COMPLETED
            )
            if pending not in done:
                return
            error = pending.exception()
            if isinstance(error, WebSocketDisconnect):
                raise error
            if error is not None:
                # Let the ordered receive loop raise it after the buffered work.
                return
            self._frames.append(pending.result())
            self._pending = None
            if stream in done:
                return

    async def _error(self, message: str, request_id: str = "") -> None:
        await self._socket.send_json(
            {"type": "error", "id": request_id, "message": message}
        )
