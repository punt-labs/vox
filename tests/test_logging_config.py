"""Tests for the unified logging owner (src/punt_vox/logging_config.py)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from punt_vox import logging_config
from punt_vox.config import ConfigStore
from punt_vox.log_append_handler import AppendLogHandler

if TYPE_CHECKING:
    from collections.abc import Iterator


def _redirect_log_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    """Redirect the log tree to tmp and restore the root logger afterward."""
    log_dir = tmp_path / "logs"
    monkeypatch.setattr(logging_config, "_LOG_DIR", log_dir)
    monkeypatch.setattr(logging_config, "_LOG_FILE", log_dir / "vox.log")
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


def _append_handlers(root: logging.Logger) -> list[AppendLogHandler]:
    return [h for h in root.handlers if isinstance(h, AppendLogHandler)]


def _no_stream_handler(root: logging.Logger) -> bool:
    """Return whether no bare stderr StreamHandler is installed (DES-046)."""
    return not any(
        isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
        for h in root.handlers
    )


class TestConfigureDaemonLogging:
    """The daemon appends to one private vox.log -- no stderr handler."""

    @pytest.fixture(autouse=True)
    def _isolate(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> Iterator[None]:
        yield from _redirect_log_tree(tmp_path, monkeypatch)

    def test_installs_one_append_handler_no_stderr(self, tmp_path: Path) -> None:
        logging_config.configure_daemon_logging()
        root = logging.getLogger()
        assert len(_append_handlers(root)) == 1
        assert _no_stream_handler(root)  # no stderr double-write (DES-046)

    def test_daemon_record_lands_in_vox_log_without_client_prefix(
        self, tmp_path: Path
    ) -> None:
        logging_config.configure_daemon_logging()
        logging.getLogger("punt_vox.voxd.router").info("daemon up")
        contents = (tmp_path / "logs" / "vox.log").read_text(encoding="utf-8")
        assert "punt_vox.voxd.router: daemon up" in contents
        assert "client." not in contents  # the daemon carries no client prefix

    def test_symlink_log_path_raises_through_dictconfig(self, tmp_path: Path) -> None:
        target = tmp_path / "target.txt"
        target.write_text("do not write here\n")
        log = tmp_path / "logs" / "vox.log"
        log.parent.mkdir(parents=True)
        log.symlink_to(target)
        logging_config.configure_daemon_logging()
        # The append sink refuses the symlink at write time (O_NOFOLLOW), routing
        # the failure to stderr rather than following the link.
        logging.getLogger("punt_vox.voxd").info("blocked")
        assert target.read_text() == "do not write here\n"


class TestConfigureClientLogging:
    """A client appends to the shared vox.log, stamped with its role."""

    @pytest.fixture(autouse=True)
    def _isolate(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> Iterator[None]:
        yield from _redirect_log_tree(tmp_path, monkeypatch)

    def test_installs_one_append_handler_no_stderr(self) -> None:
        logging_config.configure_client_logging(role="cli")
        root = logging.getLogger()
        assert len(_append_handlers(root)) == 1
        assert _no_stream_handler(root)

    def test_skip_hook_line_lands_in_vox_log_no_fallback(self, tmp_path: Path) -> None:
        """A hook that does no daemon work still lands its line in vox.log.

        No daemon is reachable in this test, yet the record appends locally -- and
        no ``vox-fallback.log`` is ever created.
        """
        logging_config.configure_client_logging(role="hook")
        logging.getLogger("punt_vox.hooks").info("stop hook skipped")
        vox_log = tmp_path / "logs" / "vox.log"
        assert "client.hook.punt_vox.hooks: stop hook skipped" in vox_log.read_text(
            encoding="utf-8"
        )
        assert not (tmp_path / "logs" / "vox-fallback.log").exists()

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


class TestShipTransportIsGone:
    """The ship transport and fallback file no longer exist (forward integration)."""

    @pytest.mark.parametrize(
        "module",
        ["log_ship", "log_flush", "log_wire", "voxd.log_sink"],
    )
    def test_deleted_modules_do_not_import(self, module: str) -> None:
        import importlib

        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(f"punt_vox.{module}")
