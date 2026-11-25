"""Uvicorn transport setup and bind notification for voxd."""

from __future__ import annotations

import asyncio
import logging
import signal
import threading
from contextlib import contextmanager
from typing import TYPE_CHECKING, Self, final

import uvicorn

from punt_vox.voxd.crash_logging import CrashLogger

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Generator
    from socket import socket

    from starlette.applications import Starlette

logger = logging.getLogger(__name__)

# Inbound WebSocket frame cap (uvicorn defaults to 16 MiB). 256 KiB comfortably
# fits the largest legitimate client->daemon frame (a music request) while
# denying multi-MB frames that would burden the daemon's event loop.
_WS_MAX_FRAME_BYTES = 256 * 1024
_STOP_SIGNALS = (signal.SIGINT, signal.SIGTERM)
"""The signals uvicorn shuts down on and re-raises after its shutdown."""


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
        self._warn_if_exposed()
        server = self._server
        server.startup = self._startup_and_publish  # type: ignore[method-assign]
        with self._stop_signals_end_normally():
            await server.serve()

    def _warn_if_exposed(self) -> None:
        host = self._server.config.host
        if host not in ("127.0.0.1", "::1", "localhost"):
            logger.warning(
                "Binding to %s -- voxd is accessible from the network. "
                "Ensure VOXD_TOKEN is set on all clients.",
                host,
            )

    @contextmanager
    def _stop_signals_end_normally(self) -> Generator[None]:
        """Make a stop signal end the process by a normal exit, not by itself.

        uvicorn shuts down on SIGINT or SIGTERM, restores the handlers it
        found, and then re-raises the signal so the process ends the way
        the sender asked. With the default handlers in place that kills
        the process after the shutdown already ran, and Python's own exit
        (atexit hooks, the coverage save under the coverage runner) never
        happens.

        The handler installed here is uvicorn's own stop handler, so a
        signal that lands before uvicorn installs its handler, or after it
        restores this one, is still recorded and still stops the server;
        the re-raise after the shutdown is absorbed the same way, and the
        process exits 0. It is a Python handler, never ``SIG_IGN``: an
        ignored signal is inherited across ``exec``, so a child spawned
        meanwhile could not be stopped by SIGTERM, while a caught one
        resets to the default in the child. Signals can be handled only on
        the main thread; off it uvicorn installs nothing, and neither does
        this.
        """
        if threading.current_thread() is not threading.main_thread():
            yield
            return
        previous = {
            sig: signal.signal(sig, self._server.handle_exit) for sig in _STOP_SIGNALS
        }
        try:
            yield
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)

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
