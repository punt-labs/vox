"""Bracket the stdio MCP server with durable lifecycle records.

``vox mcp`` is launched by an MCP client that discards stderr, so a server that
dies on import or exits on EOF must say so in the private log file, not only in
a traceback nobody keeps. Logging is configured before the server module is
imported: an incompatible MCP SDK fails that import, and the failure is still
recorded. Each record names fixed metadata only -- the outcome and an exception
class or module name -- never exception text.
"""

from __future__ import annotations

import importlib
import logging
from typing import TYPE_CHECKING, final

from punt_vox.logging_config import configure_client_logging

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["McpLifecycle"]

logger = logging.getLogger(__name__)


@final
class McpLifecycle:
    """Run the MCP server between a durable start and a durable stop record."""

    __slots__ = ()

    def run(self) -> None:
        """Import the server, run it, and record how it stopped."""
        configure_client_logging(role="mcp")
        try:
            server = importlib.import_module("punt_vox.server")
        except ImportError as exc:
            logger.error(
                "vox MCP server failed to start: outcome=failed "
                "reason=import_error module=%s",
                exc.name or "unknown",
            )
            raise
        run_server: Callable[[], None] = server.run_server
        try:
            run_server()
        except KeyboardInterrupt:
            logger.info("vox MCP server stopped: outcome=interrupted")
            raise
        except Exception as exc:
            logger.error(
                "vox MCP server stopped: outcome=failed reason=%s",
                type(exc).__name__,
            )
            raise
        logger.info("vox MCP server stopped: outcome=completed")
