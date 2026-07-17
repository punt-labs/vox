"""WebSocket message routing for voxd."""
# pyright: reportPrivateUsage=false
# Internal module within the voxd package -- cross-module private access is expected.

from __future__ import annotations

import hmac
import json
import logging
from typing import Self

from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

from punt_vox.voxd.types import MessageHandler

__all__ = ["WebSocketRouter"]

logger = logging.getLogger(__name__)


class WebSocketRouter:
    """Route WebSocket messages to handler callables for voxd."""

    __slots__ = (
        "_auth_token",
        "_client_count",
        "_handlers",
    )

    _auth_token: str | None
    _client_count: int
    _handlers: dict[str, MessageHandler]

    def __new__(
        cls,
        *,
        handlers: dict[str, MessageHandler],
        auth_token: str | None,
    ) -> Self:
        self = super().__new__(cls)
        self._handlers = handlers
        self._auth_token = auth_token
        self._client_count = 0
        return self

    # -- Properties ------------------------------------------------------------

    @property
    def client_count(self) -> int:
        """Return the number of connected WebSocket clients."""
        return self._client_count

    @property
    def handlers(self) -> dict[str, MessageHandler]:
        """Return the handler dispatch table."""
        return self._handlers

    # -- Connection handler ----------------------------------------------------

    async def handle_connection(self, websocket: WebSocket) -> None:
        """Main WebSocket route at /ws."""
        if not self._check_auth(websocket):
            # Client metadata only -- never the token, supplied or expected.
            logger.warning("Auth rejected: connection from %s", websocket.client)
            await websocket.close(code=1008)
            return

        await websocket.accept()
        self._client_count += 1
        logger.info("Client connected (total: %d)", self._client_count)

        try:
            while True:
                # Preempt Starlette's RuntimeError: a client can close the socket
                # after its "playing" ack while this loop awaits receive_text().
                if websocket.application_state != WebSocketState.CONNECTED:
                    break
                raw = await websocket.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    await websocket.send_json(
                        {"type": "error", "id": "", "message": "invalid JSON"}
                    )
                    continue

                if not isinstance(msg, dict):
                    await websocket.send_json(
                        {"type": "error", "id": "", "message": "expected JSON object"}
                    )
                    continue

                msg_type = str(msg.get("type", ""))  # pyright: ignore[reportUnknownMemberType,reportUnknownArgumentType]
                handler = self._handlers.get(msg_type)
                if handler is None:
                    msg_id = str(msg.get("id", ""))  # pyright: ignore[reportUnknownMemberType,reportUnknownArgumentType]
                    await websocket.send_json(
                        {
                            "type": "error",
                            "id": msg_id,
                            "message": f"unknown message type: {msg_type}",
                        }
                    )
                    continue

                await handler(msg, websocket)  # pyright: ignore[reportUnknownArgumentType]
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.exception("WebSocket error")
        finally:
            self._client_count -= 1
            logger.info("Client disconnected (total: %d)", self._client_count)

    # -- Auth ------------------------------------------------------------------

    def _check_auth(self, websocket: WebSocket) -> bool:
        """Verify the auth token from query param."""
        if self._auth_token is None:
            return True  # No auth configured (tests)
        token = websocket.query_params.get("token", "")
        return hmac.compare_digest(token, self._auth_token)
