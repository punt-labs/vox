"""Route uncaught exceptions to the daemon file log so a crash is never silent."""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING, Self, final

if TYPE_CHECKING:
    import asyncio
    from types import TracebackType

__all__ = ["CrashLogger"]


@final
class CrashLogger:
    """Record uncaught exceptions to a file-backed logger from both entry paths.

    The daemon logs only to its private ``vox.log`` -- no stderr handler -- so an
    uncaught traceback would otherwise vanish: launchd no longer tees stderr and
    the systemd journal receives nothing. This installs two last-resort hooks
    that route a crash to the given logger (and thus the log file):

    * :meth:`install_excepthook` covers the synchronous path -- startup wiring and
      any exception that propagates out of ``asyncio.run`` -- via ``sys.excepthook``.
    * :meth:`install_loop_handler` covers fire-and-forget task exceptions that the
      event loop would otherwise only print, via ``loop.set_exception_handler``.
    """

    __slots__ = ("_logger",)
    _logger: logging.Logger

    def __new__(cls, logger: logging.Logger) -> Self:
        self = super().__new__(cls)
        self._logger = logger
        return self

    def install_excepthook(self) -> None:
        """Route uncaught synchronous exceptions to the logger."""
        sys.excepthook = self._handle_excepthook

    def install_loop_handler(self, loop: asyncio.AbstractEventLoop) -> None:
        """Route unhandled event-loop exceptions to the logger."""
        loop.set_exception_handler(self._handle_loop_exception)

    def _handle_excepthook(
        self,
        exc_type: type[BaseException],
        exc: BaseException,
        traceback: TracebackType | None,
    ) -> None:
        """Log an uncaught synchronous exception with its full traceback.

        ``KeyboardInterrupt`` defers to the default hook so Ctrl-C keeps its
        normal, quiet exit rather than dumping a critical traceback.
        """
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, traceback)
            return
        self._logger.critical(
            "Uncaught exception -- voxd is crashing",
            exc_info=(exc_type, exc, traceback),
        )

    def _handle_loop_exception(
        self,
        _loop: asyncio.AbstractEventLoop,
        # asyncio hands the handler a heterogeneous context dict (message,
        # exception, future, handle, ...) -- object is the honest value type.
        context: dict[str, object],
    ) -> None:
        """Log an unhandled event-loop exception with its traceback."""
        message = context.get("message", "unhandled event-loop exception")
        exc = context.get("exception")
        if isinstance(exc, BaseException):
            self._logger.error("Event-loop exception: %s", message, exc_info=exc)
        else:
            self._logger.error("Event-loop error: %s", message)
