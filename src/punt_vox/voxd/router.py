"""WebSocket message routing for voxd."""
# pyright: reportPrivateUsage=false
# Internal module within the voxd package -- cross-module private access is expected.

from __future__ import annotations

import hmac
import logging
from typing import TYPE_CHECKING, Self

from starlette.websockets import WebSocket, WebSocketDisconnect

from punt_vox.voxd.ws_session import WebSocketSession

if TYPE_CHECKING:
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
        """Create a web socket router."""
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
        """Route WebSocket messages received at /ws."""
        # Browsers always send Origin on a WebSocket handshake; vox's native
        # Python clients (CLI, hooks, MCP server, remote over VOXD_HOST/SSH) never
        # do. A present Origin is therefore a scripted cross-site connection
        # (CSWSH / DNS-rebinding) with no business reaching the audio daemon --
        # reject before inspecting the token, and log metadata only.
        if "origin" in websocket.headers:
            logger.warning("Rejected: unexpected Origin from %s", websocket.client)
            await websocket.close(code=1008)
            return

        if not self._check_auth(websocket):
            # Client metadata only -- never the token, supplied or expected.
            logger.warning("Auth rejected: connection from %s", websocket.client)
            await websocket.close(code=1008)
            return

        await websocket.accept()
        self._client_count += 1
        logger.debug("Client connected (total: %d)", self._client_count)

        try:
            await WebSocketSession(websocket, self._handlers).serve()
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.exception("WebSocket error")
        finally:
            self._client_count -= 1
            logger.debug("Client disconnected (total: %d)", self._client_count)

    # -- Auth ------------------------------------------------------------------

    def _check_auth(self, websocket: WebSocket) -> bool:
        """Verify the auth token from query param."""
        if self._auth_token is None:
            return True  # No auth configured (tests)
        token = websocket.query_params.get("token", "")
        return hmac.compare_digest(token, self._auth_token)
