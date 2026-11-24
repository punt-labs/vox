"""Exercise vox policy through the shared logging configuration."""

from __future__ import annotations

import logging
import multiprocessing
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from punt_observe import logging as shared_logging
from punt_observe.logging import AtomicAppendLog, LoggingSetupError
from punt_observe.logging.handler import PrivateFileHandler

from punt_vox import logging_config, paths
from punt_vox.config import ConfigStore
from punt_vox.voxd import DaemonHealth, PlaybackQueue

if TYPE_CHECKING:
    from multiprocessing.synchronize import Barrier


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
    root.setLevel(logging.INFO)
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


def test_live_daemon_level_switch_writes_debug() -> None:
    logging_config.configure_daemon_logging()
    health = DaemonHealth(PlaybackQueue(), lambda: 0, 0)
    assert health.full_payload()["log_level"] == "info"
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    logger = logging.getLogger("punt_vox.live_daemon")
    logger.debug("hidden daemon debug")
    logging_config.apply_log_level(logging.DEBUG)
    assert health.full_payload()["log_level"] == "debug"
    logger.debug("visible daemon debug")
    logging_config.apply_log_level(logging.INFO)
    assert health.full_payload()["log_level"] == "info"
    logger.debug("hidden again daemon debug")
    contents = (paths.log_dir() / "vox.log").read_text()
    assert "visible daemon debug" in contents
    assert "hidden daemon debug" not in contents
    assert "hidden again daemon debug" not in contents
    assert all(isinstance(h, PrivateFileHandler) for h in root.handlers)


def test_daemon_level_is_unavailable_without_file_handler() -> None:
    with patch.object(logging, "getHandlerByName", return_value=None):
        assert logging_config.daemon_log_level() is None


def test_verbose_overrides_config() -> None:
    logging_config.configure_client_logging(role="cli", verbose=True)
    logging.getLogger("punt_vox.test").debug("verbose marker")
    assert "verbose marker" in (paths.log_dir() / "vox.log").read_text()


def test_unsafe_sink_is_closed(tmp_path: Path) -> None:
    path = paths.log_dir() / "vox.log"
    path.parent.mkdir(parents=True)
    target = tmp_path / "redirected.log"
    target.touch()
    path.symlink_to(target)
    with pytest.raises(LoggingSetupError):
        logging_config.configure_daemon_logging()


@pytest.mark.parametrize("role", ["cli", "mcp", "hook", "worker"])
@pytest.mark.parametrize(
    "error", [LoggingSetupError("secret-marker"), OSError("secret-marker")]
)
def test_client_setup_failure_is_open(
    role: logging_config.Role, error: OSError, capsys: pytest.CaptureFixture[str]
) -> None:
    logging_config.configure_client_logging(role=role)
    with patch.object(logging_config, "configure_logging", side_effect=error):
        logging_config.configure_client_logging(role=role)
    logging_config.apply_log_level(logging.DEBUG)
    logging.getLogger("punt_vox.failed_setup").error("must never reach stderr")
    assert all(
        isinstance(handler, logging.NullHandler)
        for handler in logging.getLogger().handlers
    )
    assert not any(
        isinstance(h, PrivateFileHandler) for h in logging.getLogger().handlers
    )
    assert capsys.readouterr().err == (
        "vox: private logging unavailable; continuing without file\n"
    )


def _append_process_records(writer: int, barrier: Barrier) -> None:
    logging_config.configure_client_logging(role="worker")
    handler = logging.getHandlerByName("file")
    assert isinstance(handler, PrivateFileHandler)
    handler.sink = AtomicAppendLog(
        paths.log_dir() / "vox.log", max_bytes=2048, backup_count=100
    )
    barrier.wait(timeout=30)
    for sequence in range(50):
        logging.getLogger("punt_vox.concurrent").info(
            "writer=%d record=%d payload=%s", writer, sequence, "x" * 128
        )


@pytest.mark.slow
def test_multiple_configured_processes_rotate_without_losing_records() -> None:
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(4)
    processes = [
        context.Process(target=_append_process_records, args=(writer, barrier))
        for writer in range(4)
    ]
    try:
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=45)
            assert process.exitcode == 0
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
    directory = paths.log_dir()
    logs = [directory / "vox.log", *directory.glob("vox.log.[0-9]*")]
    assert len(logs) > 1
    actual = Counter(
        line.split("writer=", 1)[1]
        for path in logs
        for line in path.read_text().splitlines()
    )
    expected = Counter(
        f"{writer} record={sequence} payload={'x' * 128}"
        for writer in range(4)
        for sequence in range(50)
    )
    assert actual == expected
    assert directory.stat().st_mode & 0o777 == 0o700
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in directory.iterdir())


@pytest.mark.parametrize(
    "error", [LoggingSetupError("secret-marker"), OSError("secret-marker")]
)
def test_daemon_setup_failure_propagates(error: OSError) -> None:
    with (
        patch.object(logging_config, "configure_logging", side_effect=error),
        pytest.raises(OSError) as raised,
    ):
        logging_config.configure_daemon_logging()
    assert raised.value is error


def test_health_before_first_record() -> None:
    assert logging_config.log_health() == {"name": "vox.log", "status": "healthy"}


def test_redaction_and_suppression() -> None:
    logging_config.configure_client_logging(role="cli", verbose=True)
    logging.getLogger("httpx").info("suppressed marker")
    logging.getLogger("punt_vox.test").warning("https://host/path?token=secret-marker")
    contents = (paths.log_dir() / "vox.log").read_text()
    assert "suppressed marker" not in contents
    assert "secret-marker" not in contents
