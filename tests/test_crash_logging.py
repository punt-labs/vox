"""Tests for punt_vox.voxd.crash_logging -- uncaught exceptions reach vox.log."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from punt_vox.append_log import AtomicAppendLog
from punt_vox.log_append_handler import AppendLogHandler
from punt_vox.voxd.crash_logging import CrashLogger


def _file_logger(tmp_path: Path) -> tuple[logging.Logger, Path]:
    """Return an isolated logger appending to a private ``vox.log`` under tmp."""
    log_file = tmp_path / "vox.log"
    handler = AppendLogHandler.bind(AtomicAppendLog(log_file), level="DEBUG")
    logger = logging.getLogger(f"punt_vox.test.crash.{tmp_path.name}")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    logger.handlers[:] = [handler]
    return logger, log_file


def _raise_and_capture() -> tuple[type[BaseException], BaseException]:
    """Raise and catch a ValueError so it carries a real traceback."""
    try:
        raise ValueError("boom-uncaught-marker")
    except ValueError as exc:
        return type(exc), exc


class TestExcepthook:
    """The sync last-resort hook records an uncaught exception to the file."""

    def test_uncaught_exception_is_written_to_the_log_file(
        self, tmp_path: Path
    ) -> None:
        logger, log_file = _file_logger(tmp_path)
        crash = CrashLogger(logger)
        original = sys.excepthook
        crash.install_excepthook()
        try:
            exc_type, exc = _raise_and_capture()
            sys.excepthook(exc_type, exc, exc.__traceback__)
        finally:
            sys.excepthook = original
            for handler in logger.handlers:
                handler.close()

        text = log_file.read_text()
        assert "voxd is crashing" in text
        assert "boom-uncaught-marker" in text
        assert "ValueError" in text  # the traceback line is present

    def test_keyboard_interrupt_defers_to_default_hook(self, tmp_path: Path) -> None:
        """Ctrl-C keeps its quiet exit -- no critical record in the file."""
        logger, log_file = _file_logger(tmp_path)
        crash = CrashLogger(logger)
        original = sys.excepthook
        crash.install_excepthook()
        try:
            exc = KeyboardInterrupt()
            sys.excepthook(KeyboardInterrupt, exc, exc.__traceback__)
        finally:
            sys.excepthook = original
            for handler in logger.handlers:
                handler.close()

        # The append sink creates the file lazily, so a deferred Ctrl-C leaves no
        # log at all -- the strongest form of "nothing was written".
        assert not log_file.exists()


class TestBootstrapExcepthook:
    """The bootstrap hook captures a crash before any logging is configured."""

    def test_bootstrap_hook_lands_without_dictconfig(self, tmp_path: Path) -> None:
        """A crash before ``dictConfig`` runs still lands in vox-boot.log.

        The emergency sink writes with raw ``os`` syscalls, so no file handler and
        no logger need exist -- the exact case (logging construction itself
        raising) the daemon's boot sequence must survive.
        """
        boot = tmp_path / "vox-boot.log"
        crash = CrashLogger(logging.getLogger("punt_vox.test.boot.unused"))
        original = sys.excepthook
        crash.install_bootstrap_excepthook(boot)
        try:
            exc_type, exc = _raise_and_capture()
            sys.excepthook(exc_type, exc, exc.__traceback__)
        finally:
            sys.excepthook = original

        text = boot.read_text(encoding="utf-8")
        assert "boom-uncaught-marker" in text
        assert "ValueError" in text
        assert (boot.stat().st_mode & 0o077) == 0  # 0600 emergency file

    def test_bootstrap_keyboard_interrupt_defers(self, tmp_path: Path) -> None:
        boot = tmp_path / "vox-boot.log"
        crash = CrashLogger(logging.getLogger("punt_vox.test.boot.ki"))
        original = sys.excepthook
        crash.install_bootstrap_excepthook(boot)
        try:
            exc = KeyboardInterrupt()
            sys.excepthook(KeyboardInterrupt, exc, exc.__traceback__)
        finally:
            sys.excepthook = original
        assert not boot.exists()  # nothing written for a quiet Ctrl-C


class TestLoopExceptionHandler:
    """The event-loop hook records a fire-and-forget task exception to the file."""

    def test_loop_exception_is_written_to_the_log_file(self, tmp_path: Path) -> None:
        logger, log_file = _file_logger(tmp_path)
        crash = CrashLogger(logger)
        loop = asyncio.new_event_loop()
        try:
            crash.install_loop_handler(loop)
            _, exc = _raise_and_capture()
            loop.call_exception_handler({"message": "task blew up", "exception": exc})
        finally:
            loop.close()
            for handler in logger.handlers:
                handler.close()

        text = log_file.read_text()
        assert "task blew up" in text
        assert "boom-uncaught-marker" in text

    def test_loop_error_without_exception_is_still_logged(self, tmp_path: Path) -> None:
        """A context with a message but no exception still records the error."""
        logger, log_file = _file_logger(tmp_path)
        crash = CrashLogger(logger)
        loop = asyncio.new_event_loop()
        try:
            crash.install_loop_handler(loop)
            loop.call_exception_handler({"message": "socket closed abruptly"})
        finally:
            loop.close()
            for handler in logger.handlers:
                handler.close()

        assert "socket closed abruptly" in log_file.read_text()
