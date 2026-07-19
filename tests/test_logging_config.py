"""Tests for the unified logging owner (src/punt_vox/logging_config.py)."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from punt_vox import logging_config
from punt_vox.config import ConfigStore
from punt_vox.log_handlers import PrivateRotatingFileHandler
from punt_vox.log_ship import DaemonLogHandler

if TYPE_CHECKING:
    from collections.abc import Iterator


def _redirect_log_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    """Redirect the log tree to tmp and restore the root logger afterward."""
    log_dir = tmp_path / "logs"
    monkeypatch.setattr(logging_config, "_LOG_DIR", log_dir)
    monkeypatch.setattr(logging_config, "_LOG_FILE", log_dir / "vox.log")
    monkeypatch.setattr(logging_config, "_FALLBACK_FILE", log_dir / "vox-fallback.log")
    # Default: level resolves to the quiet INFO. Individual tests override.
    _pin_level(monkeypatch, "info")
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    yield
    for handler in root.handlers[:]:
        handler.close()
    root.handlers[:] = saved_handlers
    root.setLevel(saved_level)


def _pin_level(monkeypatch: pytest.MonkeyPatch, level: str) -> None:
    """Pin the resolved log level so a test's env/config can't leak in.

    ``resolve_log_level`` is a classmethod, so the replacement must be one too --
    a zero-arg lambda takes ``cls`` as its first positional and raises on an
    instance-bound call.
    """
    monkeypatch.setattr(
        ConfigStore, "resolve_log_level", classmethod(lambda _cls: level)
    )


def _file_handler(root: logging.Logger) -> PrivateRotatingFileHandler:
    handlers = [h for h in root.handlers if isinstance(h, PrivateRotatingFileHandler)]
    assert len(handlers) == 1
    return handlers[0]


class TestConfigureDaemonLogging:
    """The daemon is the single writer of a private vox.log -- no stderr handler."""

    @pytest.fixture(autouse=True)
    def _isolate(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> Iterator[None]:
        yield from _redirect_log_tree(tmp_path, monkeypatch)

    def test_installs_one_private_file_handler(self, tmp_path: Path) -> None:
        logging_config.configure_daemon_logging()
        root = logging.getLogger()
        handler = _file_handler(root)
        assert Path(handler.baseFilename) == tmp_path / "logs" / "vox.log"
        streams = [
            h
            for h in root.handlers
            if isinstance(h, logging.StreamHandler)
            and not isinstance(h, logging.FileHandler)
        ]
        assert streams == []  # no stderr double-write

    def test_untightenable_log_is_warned_in_the_live_log(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        log = tmp_path / "logs" / "vox.log"
        log.parent.mkdir(parents=True)
        log.write_text("existing\n")

        def _deny_fchmod(fd: int, mode: int) -> None:
            raise PermissionError("cannot fchmod")

        monkeypatch.setattr(os, "fchmod", _deny_fchmod)
        logging_config.configure_daemon_logging()
        contents = log.read_text()
        assert "could not enforce 0600 on log file(s)" in contents
        assert "vox.log" in contents

    def test_symlink_log_path_raises_through_dictconfig(self, tmp_path: Path) -> None:
        target = tmp_path / "target.txt"
        target.write_text("do not write here\n")
        log = tmp_path / "logs" / "vox.log"
        log.parent.mkdir(parents=True)
        log.symlink_to(target)
        with pytest.raises(ValueError):  # dictConfig wraps the O_NOFOLLOW OSError
            logging_config.configure_daemon_logging()
        assert target.read_text() == "do not write here\n"


class TestConfigureClientLogging:
    """A client ships records over the socket -- it holds no file handler."""

    @pytest.fixture(autouse=True)
    def _isolate(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> Iterator[None]:
        yield from _redirect_log_tree(tmp_path, monkeypatch)

    def test_installs_ship_handler_no_file(self) -> None:
        logging_config.configure_client_logging(role="cli")
        root = logging.getLogger()
        assert any(isinstance(h, DaemonLogHandler) for h in root.handlers)
        assert not any(isinstance(h, logging.FileHandler) for h in root.handlers)

    def test_verbose_raises_root_to_debug(self) -> None:
        logging_config.configure_client_logging(role="cli", verbose=True)
        assert logging.getLogger().level == logging.DEBUG

    def test_default_level_is_info(self) -> None:
        logging_config.configure_client_logging(role="cli")
        assert logging.getLogger().level == logging.INFO

    def test_reapply_picks_up_a_level_change(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A long-lived client re-reads the level and flips root + handler live."""
        _pin_level(monkeypatch, "info")
        logging_config.configure_client_logging(role="mcp")
        root = logging.getLogger()
        assert root.level == logging.INFO

        _pin_level(monkeypatch, "debug")  # e.g. `vox log debug` on another process
        logging_config.reapply_client_log_level()

        assert root.level == logging.DEBUG
        assert all(h.level == logging.DEBUG for h in root.handlers)

    def test_config_log_level_debug_applies(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A resolved ``debug`` level raises the client root to DEBUG."""
        _pin_level(monkeypatch, "debug")
        logging_config.configure_client_logging(role="mcp")
        assert logging.getLogger().level == logging.DEBUG

    def test_client_config_tightens_loose_log_dir(self, tmp_path: Path) -> None:
        """A pre-existing 0755 logs/ is re-tightened to 0700 on configure."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        log_dir.chmod(0o755)
        logging_config.configure_client_logging(role="hook")
        assert (log_dir.stat().st_mode & 0o077) == 0

    def test_client_config_survives_untightenable_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A dir the client cannot chmod never crashes configure -- best effort."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        def _deny(_self: object, _mode: int) -> None:
            raise PermissionError("cannot chmod")

        monkeypatch.setattr("punt_vox.private_state.Path.chmod", _deny)
        logging_config.configure_client_logging(role="hook")  # no exception

    def test_mcp_framework_logger_suppressed(self) -> None:
        """The mcp framework request logger is pinned to WARNING (no INFO noise)."""
        logging_config.configure_client_logging(role="mcp")
        assert logging.getLogger("mcp.server.lowlevel").level == logging.WARNING
        assert logging.getLogger("mcp").level == logging.WARNING
