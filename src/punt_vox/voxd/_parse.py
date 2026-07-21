"""Low-level wire-frame helpers for voxd handlers.

Inbound: extract optional typed values from a message dict. Outbound: send a
reply frame in a way that survives a client that has already disconnected --
grouped here as the one place low-level WebSocket-frame marshalling lives, so
every handler shares one parse path and one disconnect-safe send path.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from starlette.websockets import WebSocketDisconnect

if TYPE_CHECKING:
    from starlette.websockets import WebSocket

__all__ = [
    "parse_optional_float",
    "parse_optional_int",
    "parse_optional_str",
    "safe_send",
]

logger = logging.getLogger(__name__)


def parse_optional_float(msg: dict[str, object], key: str) -> float | None:
    """Extract an optional float field from a message dict."""
    raw = msg.get(key)
    if raw is None:
        return None
    return float(str(raw))


def parse_optional_int(msg: dict[str, object], key: str) -> int | None:
    """Extract an optional int field from a message dict."""
    raw = msg.get(key)
    if raw is None:
        return None
    return int(str(raw))


def parse_optional_str(msg: dict[str, object], key: str) -> str | None:
    """Extract an optional string field, returning None for empty strings."""
    raw = str(msg.get(key, ""))
    return raw or None


async def safe_send(websocket: WebSocket, payload: dict[str, object]) -> bool:
    """Send *payload* as JSON; return True if delivered, False if the peer had gone.

    A client that closes before or during a reply must end the request quietly,
    not surface as a traceback through the router's broad ``except``.
    ``WebSocketDisconnect`` is the expected closed-client signal (silent); a
    ``RuntimeError`` from a send on an already-closed socket is debug-logged so a
    genuine send fault is not swallowed invisibly. The bool return lets a caller
    skip further work once the peer is gone.
    """
    try:
        await websocket.send_json(payload)
    except WebSocketDisconnect:
        return False
    except RuntimeError as exc:
        logger.debug("send dropped (client closed?): %s", exc)
        return False
    return True
