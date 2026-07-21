"""Disconnect-safe WebSocket send shared by the voxd handlers.

A client that closes before or during a reply must end the request quietly, not
surface as a traceback through the router's broad ``except``. ``WebSocketDisconnect``
is the expected closed-client signal (silent); a ``RuntimeError`` from a send on
an already-closed socket is debug-logged so a genuine send fault is not swallowed
invisibly. The bool return lets a caller skip further work once the peer is gone.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from starlette.websockets import WebSocketDisconnect

if TYPE_CHECKING:
    from starlette.websockets import WebSocket

__all__ = ["safe_send"]

logger = logging.getLogger(__name__)


async def safe_send(websocket: WebSocket, payload: dict[str, object]) -> bool:
    """Send *payload* as JSON; return True if delivered, False if the peer had gone."""
    try:
        await websocket.send_json(payload)
    except WebSocketDisconnect:
        return False
    except RuntimeError as exc:
        logger.debug("send dropped (client closed?): %s", exc)
        return False
    return True
