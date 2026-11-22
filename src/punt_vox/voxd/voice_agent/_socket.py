"""Open and use a signed socket without logging the bearer URL or frames."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosedOK, WebSocketException

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
    async def open(signed_url: str) -> ClientConnection:
        """Open the vendor socket (or loopback for the wire tests)."""
        try:
            url = urlsplit(signed_url)
            origin = (url.scheme, url.hostname)
        except ValueError:
            msg = "Invalid ElevenLabs signed URL"
            raise ConvAIError(msg) from None
        trusted = {
            ("wss", "api.elevenlabs.io"),
            ("ws", "127.0.0.1"),
            ("ws", "::1"),
        }
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
        except (WebSocketException, OSError, ValueError) as exc:
            msg = f"ElevenLabs WebSocket handshake failed ({type(exc).__name__})"
            raise ConvAIError(msg) from None

    @staticmethod
    async def receive(socket: ClientConnection) -> bytes | str:
        """Read a bounded message; raise EOFError on a normal remote close."""
        try:
            return await socket.recv()
        except ConnectionClosedOK:
            msg = "ElevenLabs ended the conversation"
            raise EOFError(msg) from None
        except WebSocketException as exc:
            msg = f"ElevenLabs WebSocket receive failed ({type(exc).__name__})"
            raise ConvAIError(msg) from None

    @staticmethod
    async def send(socket: ClientConnection, payload: dict[str, object]) -> None:
        """Write a text frame without exposing payloads in an error."""
        try:
            await socket.send(json.dumps(payload))
        except WebSocketException as exc:
            msg = f"ElevenLabs WebSocket send failed ({type(exc).__name__})"
            raise ConvAIError(msg) from None
