"""Route uncaught exceptions to the daemon file log so a crash is never silent."""

from __future__ import annotations

import logging
import sys
import traceback
from typing import TYPE_CHECKING, Self, final

from punt_vox.append_log import AtomicAppendLog

if TYPE_CHECKING:
    import asyncio
    from pathlib import Path
    from types import TracebackType

__all__ = ["CrashLogger"]


@final
class CrashLogger:
    """Record uncaught exceptions to a file-backed logger from both entry paths.

    The daemon logs only to its private ``vox.log`` -- no stderr handler -- so an
    uncaught traceback would otherwise vanish: launchd no longer tees stderr and
    the systemd journal receives nothing. This installs two last-resort hooks
    that route a crash to a durable file:

    * :meth:`install_bootstrap_excepthook` covers the *earliest* startup, before
      the file handler exists: it writes the traceback straight to a dedicated
      emergency file via :class:`AtomicAppendLog`, which depends only on
      ``os.open``/``os.write`` -- so even a total logging-construction failure
      (``dictConfig`` raising) is captured, not swallowed into a void.
    * :meth:`install_excepthook` upgrades the synchronous path to the file-backed
      logger once ``vox.log`` is live -- startup wiring and any exception that
      propagates out of ``asyncio.run`` -- via ``sys.excepthook``.
    * :meth:`install_loop_handler` covers fire-and-forget task exceptions that the
      event loop would otherwise only print, via ``loop.set_exception_handler``.
    """

    __slots__ = ("_emergency", "_logger")
    _logger: logging.Logger
    _emergency: AtomicAppendLog | None

    def __new__(cls, logger: logging.Logger) -> Self:
        self = super().__new__(cls)
        self._logger = logger
        self._emergency = None
        return self

    def install_bootstrap_excepthook(self, emergency_path: Path) -> None:
        """Route uncaught exceptions to *emergency_path* before logging exists.

        Install this FIRST, before ``dictConfig``: the emergency sink writes with
        raw ``os`` syscalls, so a crash during ``ensure_user_dirs`` or logging
        construction still lands in a dedicated, near-empty file rather than
        vanishing (the daemon has no stderr). :meth:`install_excepthook` replaces
        it once the file handler is live.
        """
        self._emergency = AtomicAppendLog(emergency_path)
        sys.excepthook = self._handle_bootstrap

    def install_excepthook(self) -> None:
        """Route uncaught synchronous exceptions to the file-backed logger."""
        sys.excepthook = self._handle_excepthook

    def install_loop_handler(self, loop: asyncio.AbstractEventLoop) -> None:
        """Route unhandled event-loop exceptions to the logger."""
        loop.set_exception_handler(self._handle_loop_exception)

    def _handle_bootstrap(
        self,
        exc_type: type[BaseException],
        exc: BaseException,
        tb: TracebackType | None,
    ) -> None:
        """Write the traceback to the emergency file, one escaped line per line.

        ``KeyboardInterrupt`` defers to the default hook. Each physical traceback
        line is appended separately (the sink keeps each to one line), so a
        multi-frame traceback stays readable in the emergency file.
        """
        if issubclass(exc_type, KeyboardInterrupt) or self._emergency is None:
            sys.__excepthook__(exc_type, exc, tb)
            return
        for chunk in traceback.format_exception(exc_type, exc, tb):
            for physical in chunk.splitlines():
                self._emergency.append(physical)

    def _handle_excepthook(
        self,
        exc_type: type[BaseException],
        exc: BaseException,
        tb: TracebackType | None,
    ) -> None:
        """Log an uncaught synchronous exception with its full traceback.

        ``KeyboardInterrupt`` defers to the default hook so Ctrl-C keeps its
        normal, quiet exit rather than dumping a critical traceback.
        """
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        self._logger.critical(
            "Uncaught exception -- voxd is crashing",
            exc_info=(exc_type, exc, tb),
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
