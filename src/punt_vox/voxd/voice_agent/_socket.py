"""Open and use a signed socket without logging the bearer URL or frames."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from websockets.asyncio.client import connect
from websockets.exceptions import (
    ConnectionClosed,
    ConnectionClosedOK,
    InvalidStatus,
    WebSocketException,
)

from punt_vox.voxd.voice_agent.fault import ConvAIError

if TYPE_CHECKING:
    from websockets.asyncio.client import ClientConnection

MAX_MESSAGE_BYTES = 1024 * 1024

_PRIVATE_WIRE_LOG = logging.getLogger(f"{__name__}.wire")
_PRIVATE_WIRE_LOG.addHandler(logging.NullHandler())
_PRIVATE_WIRE_LOG.propagate = False
_PRIVATE_WIRE_LOG.setLevel(logging.CRITICAL + 1)


class _NoRedirect(connect):
    def process_redirect(self, exc: Exception) -> Exception:
        return exc


class Socket:
    """Keep socket failures at the wire boundary, with sanitized diagnostics."""

    @staticmethod
    async def open(
        signed_url: str, *, allow_loopback: bool = False
    ) -> ClientConnection:
        """Open the vendor socket; allow plaintext loopback tests only by opt-in."""
        try:
            url = urlsplit(signed_url)
            origin = (url.scheme, url.hostname)
        except ValueError:
            msg = "Invalid ElevenLabs signed URL"
            raise ConvAIError(msg) from None
        trusted = {("wss", "api.elevenlabs.io")}
        if allow_loopback:
            trusted.update({("ws", "127.0.0.1"), ("ws", "::1")})
        if origin not in trusted or url.username is not None:
            msg = "Untrusted ElevenLabs signed URL origin"
            raise ConvAIError(msg)
        try:
            return await _NoRedirect(
                signed_url,
                max_size=MAX_MESSAGE_BYTES,
                close_timeout=2,
                logger=_PRIVATE_WIRE_LOG,
            )
        except InvalidStatus as exc:
            code = exc.response.status_code
            msg = f"ElevenLabs WebSocket handshake failed (HTTP {code})"
            raise ConvAIError(msg) from None
        except (WebSocketException, OSError, ValueError) as exc:
            msg = f"ElevenLabs WebSocket handshake failed ({type(exc).__name__})"
            raise ConvAIError(msg) from None

    @staticmethod
    async def receive(socket: ClientConnection) -> bytes | str:
        """Read a bounded message; raise EOFError on a normal remote close."""
        try:
            return await socket.recv()
        except WebSocketException as exc:
            raise Socket._transfer_error("receive", exc) from None

    @staticmethod
    async def send(socket: ClientConnection, payload: dict[str, object]) -> None:
        """Write a text frame without exposing payloads in an error."""
        try:
            await socket.send(json.dumps(payload))
        except WebSocketException as exc:
            raise Socket._transfer_error("send", exc) from None

    @staticmethod
    def _transfer_error(operation: str, exc: WebSocketException) -> Exception:
        if isinstance(exc, ConnectionClosedOK):
            return EOFError("ElevenLabs ended the conversation")
        detail = type(exc).__name__
        if isinstance(exc, ConnectionClosed) and exc.rcvd is not None:
            detail = f"close code {exc.rcvd.code}"
        return ConvAIError(f"ElevenLabs WebSocket {operation} failed ({detail})")
