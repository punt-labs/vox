"""Uvicorn transport setup and bind notification for voxd."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Self, final

import uvicorn

from punt_vox.voxd.crash_logging import CrashLogger

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from socket import socket

    from starlette.applications import Starlette

logger = logging.getLogger(__name__)

# Inbound WebSocket frame cap (uvicorn defaults to 16 MiB). 256 KiB comfortably
# fits the largest legitimate client->daemon frame (a music request) while
# denying multi-MB frames that would burden the daemon's event loop.
_WS_MAX_FRAME_BYTES = 256 * 1024


@final
class DaemonServer:
    """Serve voxd and notify its owner after the listening socket binds."""

    __slots__ = ("_publish_port", "_server", "_startup")

    _publish_port: Callable[[int], None]
    _server: uvicorn.Server
    _startup: Callable[[list[socket] | None], Awaitable[None]]

    def __new__(
        cls, app: Starlette, host: str, port: int, publish_port: Callable[[int], None]
    ) -> Self:
        """Configure the ASGI transport and its bind callback."""
        self = super().__new__(cls)
        self._server = uvicorn.Server(
            uvicorn.Config(
                app,
                host=host,
                port=port,
                log_config=None,
                log_level="warning",
                access_log=True,
                ws_max_size=_WS_MAX_FRAME_BYTES,
            )
        )
        self._publish_port = publish_port
        self._startup = self._server.startup
        return self

    async def serve(self) -> None:
        """Serve until shutdown, publishing the port after startup."""
        # Route fire-and-forget task exceptions to vox.log -- with no stderr
        # handler, the loop's default printer would otherwise write to nowhere.
        CrashLogger(logger).install_loop_handler(asyncio.get_running_loop())
        server = self._server
        if server.config.host not in ("127.0.0.1", "::1", "localhost"):
            logger.warning(
                "Binding to %s -- voxd is accessible from the network. "
                "Ensure VOXD_TOKEN is set on all clients.",
                server.config.host,
            )
        server.startup = self._startup_and_publish  # type: ignore[method-assign]
        await server.serve()

    async def _startup_and_publish(self, sockets: list[socket] | None = None) -> None:
        await self._startup(sockets)
        server = self._server
        if server.should_exit or not server.servers or not server.servers[0].sockets:
            logger.error("Server started but no bound sockets; shutting down")
            server.should_exit = True
            return
        port = server.servers[0].sockets[0].getsockname()[1]
        self._publish_port(port)
        logger.info("voxd listening on http://%s:%d", server.config.host, port)
