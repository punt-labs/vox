"""Exercise vox policy through the shared logging configuration."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from punt_observe import logging as shared_logging
from punt_observe.logging import LoggingSetupError
from punt_observe.logging.handler import PrivateFileHandler

from punt_vox import logging_config, paths
from punt_vox.config import ConfigStore


@pytest.fixture(autouse=True)
def isolated_logging(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Restore process logging and pin the ordinary config level."""
    root = logging.getLogger()
    handlers, level, fallback = root.handlers[:], root.level, logging.lastResort
    monkeypatch.setattr(
        ConfigStore, "resolve_log_level", classmethod(lambda cls: "info")
    )
    monkeypatch.setattr(logging_config, "log_dir", paths.log_dir)
    yield
    for handler in root.handlers:
        handler.close()
    root.handlers[:] = handlers
    root.setLevel(level)
    logging.lastResort = fallback
    timer = logging.getLogger(logging_config._TURN_TIMER_LOGGER_NAME)
    for handler in timer.handlers:
        handler.close()
    timer.handlers.clear()
    timer.propagate = True


@pytest.mark.parametrize("role", ["cli", "mcp", "hook", "worker"])
def test_client_role_and_private_record(role: logging_config.Role) -> None:
    with patch.object(
        logging_config, "configure_logging", wraps=shared_logging.configure_logging
    ) as configure:
        logging_config.configure_client_logging(role=role)
    configure.assert_called_once_with(
        "vox", role, level="INFO", quiet=logging_config._SUPPRESSED
    )
    logging.getLogger("punt_vox.test").info("on-disk marker")
    path = paths.log_dir() / "vox.log"
    assert path == Path.home() / ".punt-labs/vox/logs/vox.log"
    assert f"client.{role}.punt_vox.test: on-disk marker" in path.read_text()
    assert path.stat().st_mode & 0o777 == 0o600
    for directory in (path.parent, path.parent.parent):
        assert directory.stat().st_mode & 0o777 == 0o700


def test_daemon_has_no_stderr() -> None:
    with patch.object(
        logging_config, "configure_logging", wraps=shared_logging.configure_logging
    ) as configure:
        logging_config.configure_daemon_logging()
    configure.assert_called_once_with(
        "vox", "daemon", level="INFO", quiet=logging_config._SUPPRESSED
    )
    assert all(isinstance(h, PrivateFileHandler) for h in logging.getLogger().handlers)
    logging.getLogger("punt_vox.test").warning("daemon marker")
    contents = (paths.log_dir() / "vox.log").read_text()
    assert "punt_vox.test: daemon marker" in contents
    assert "client." not in contents
    assert isinstance(logging.lastResort, logging.NullHandler)


def test_level_switch_changes_file_only(monkeypatch: pytest.MonkeyPatch) -> None:
    logging_config.configure_client_logging(role="mcp")
    root = logging.getLogger()
    stderr = next(h for h in root.handlers if isinstance(h, logging.StreamHandler))
    logger = logging.getLogger("punt_vox.test")
    logger.debug("hidden marker")
    monkeypatch.setattr(
        ConfigStore, "resolve_log_level", classmethod(lambda cls: "debug")
    )
    logging_config.reapply_client_log_level()
    logger.debug("visible marker")
    assert stderr.level == logging.WARNING
    assert root.level == logging.DEBUG
    contents = (paths.log_dir() / "vox.log").read_text()
    assert "hidden marker" not in contents
    assert "visible marker" in contents
    logging_config.apply_log_level(logging.INFO)
    assert (
        next(h for h in root.handlers if isinstance(h, PrivateFileHandler)).level
        == logging.INFO
    )


def test_verbose_overrides_config() -> None:
    logging_config.configure_client_logging(role="cli", verbose=True)
    logging.getLogger("punt_vox.test").debug("verbose marker")
    assert "verbose marker" in (paths.log_dir() / "vox.log").read_text()


@pytest.mark.parametrize("echo", [False, True])
def test_turn_timer_always_debug(
    echo: bool, capsys: pytest.CaptureFixture[str]
) -> None:
    logging_config.configure_client_logging(role="cli")
    logging_config.configure_turn_timer_logging(echo_to_console=echo)
    logging_config.configure_turn_timer_logging(echo_to_console=echo)
    timer = logging.getLogger(logging_config._TURN_TIMER_LOGGER_NAME)
    timer.debug("turn marker")
    contents = (paths.log_dir() / "vox.log").read_text()
    assert contents.count("turn marker") == 1
    assert "client.cli." in contents
    assert ("turn marker" in capsys.readouterr().err) is echo


def test_unsafe_sink_is_closed(tmp_path: Path) -> None:
    path = paths.log_dir() / "vox.log"
    path.parent.mkdir(parents=True)
    target = tmp_path / "redirected.log"
    target.touch()
    path.symlink_to(target)
    with pytest.raises(LoggingSetupError):
        logging_config.configure_daemon_logging()


def test_health_before_first_record() -> None:
    assert logging_config.log_health() == {"name": "vox.log", "status": "healthy"}


def test_redaction_and_suppression() -> None:
    logging_config.configure_client_logging(role="cli", verbose=True)
    logging.getLogger("httpx").info("suppressed marker")
    logging.getLogger("punt_vox.test").warning("https://host/path?token=secret-marker")
    contents = (paths.log_dir() / "vox.log").read_text()
    assert "suppressed marker" not in contents
    assert "secret-marker" not in contents
