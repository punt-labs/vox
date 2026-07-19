"""Tests for punt_vox.logging_config.configure_logging."""

from __future__ import annotations

import io
import logging
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from punt_vox import logging_config

if TYPE_CHECKING:
    from collections.abc import Iterator


def _stderr_handler(root: logging.Logger) -> logging.Handler:
    """Return the bare stderr StreamHandler installed on ``root``."""
    streams = [
        h
        for h in root.handlers
        if isinstance(h, logging.StreamHandler)
        and not isinstance(h, logging.FileHandler)
    ]
    assert len(streams) == 1
    return streams[0]


class TestConfigureLogging:
    """stderr_level is case-tolerant and rejects unknown levels clearly."""

    @pytest.fixture(autouse=True)
    def _isolate(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> Iterator[None]:
        monkeypatch.setattr(logging_config, "_LOG_DIR", tmp_path)
        monkeypatch.setattr(logging_config, "_LOG_FILE", tmp_path / "tts.log")
        # Pin sys.stderr to a stable in-memory stream: the config installs a
        # live ``ext://sys.stderr`` handler, and pytest's capture object is
        # closed/replaced across the suite, which would flake handler setup.
        monkeypatch.setattr(sys, "stderr", io.StringIO())
        root = logging.getLogger()
        saved_handlers = root.handlers[:]
        saved_level = root.level
        yield
        for handler in root.handlers[:]:
            handler.close()
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)

    def test_lowercase_level_is_normalized(self) -> None:
        logging_config.configure_logging(stderr_level="warning")

        assert _stderr_handler(logging.getLogger()).level == logging.WARNING

    def test_unknown_level_raises_clear_value_error(self) -> None:
        with pytest.raises(ValueError, match="bogus") as exc_info:
            logging_config.configure_logging(stderr_level="bogus")

        message = str(exc_info.value)
        assert "WARNING" in message  # valid set is named

    def test_canonical_level_is_unchanged(self) -> None:
        logging_config.configure_logging(stderr_level="DEBUG")

        root = logging.getLogger()
        assert _stderr_handler(root).level == logging.DEBUG
        assert root.level == logging.DEBUG

    def test_default_level_configures_root_at_info(self) -> None:
        logging_config.configure_logging()

        root = logging.getLogger()
        assert _stderr_handler(root).level == logging.WARNING
        assert root.level == logging.INFO

    def test_untightenable_log_is_warned_in_the_live_log(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A file that cannot be chmod'd surfaces as a WARNING in the live log.

        The failure must reach a durable, greppable sink -- not vanish. After
        ``configure_logging`` the file handler is live, so the post-configure
        warning lands in ``tts.log`` itself, naming the still-loose path.
        """
        (tmp_path / "tts.log").write_text("existing\n")

        def _deny_fchmod(fd: int, mode: int) -> None:
            raise PermissionError("cannot fchmod")

        monkeypatch.setattr(os, "fchmod", _deny_fchmod)

        logging_config.configure_logging()

        contents = (tmp_path / "tts.log").read_text()
        assert "could not enforce 0600 on log file(s)" in contents
        assert "tts.log" in contents

    def test_symlink_log_path_raises_through_dictconfig(self, tmp_path: Path) -> None:
        """A symlink at the log path aborts configuration -- the error is not eaten.

        ``O_NOFOLLOW`` makes the handler's ``_open`` raise on a symlink;
        ``dictConfig`` wraps a handler-construction failure in ``ValueError``,
        so misconfiguration fails loud instead of writing through the link.
        """
        target = tmp_path / "target.txt"
        target.write_text("do not write here\n")
        (tmp_path / "tts.log").symlink_to(target)

        with pytest.raises(ValueError):  # dictConfig wraps the O_NOFOLLOW OSError
            logging_config.configure_logging()

        assert target.read_text() == "do not write here\n"
