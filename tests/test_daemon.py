"""Tests for punt_vox.daemon — daemon ASGI app."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from starlette.testclient import TestClient

from punt_vox.daemon import (
    DaemonContext,
    _dispatch_hook,  # pyright: ignore[reportPrivateUsage]
    _resolve_config_from_session_key,  # pyright: ignore[reportPrivateUsage]
    build_app,
    resolve_cwd_from_pid,
)

# ---------------------------------------------------------------------------
# DaemonContext.should_play (audio deduplication)
# ---------------------------------------------------------------------------


def test_should_play_first_call() -> None:
    ctx = DaemonContext()
    assert ctx.should_play("key1") is True


def test_should_play_duplicate_within_window() -> None:
    ctx = DaemonContext()
    assert ctx.should_play("key1") is True
    assert ctx.should_play("key1") is False


def test_should_play_different_keys() -> None:
    ctx = DaemonContext()
    assert ctx.should_play("key1") is True
    assert ctx.should_play("key2") is True


def test_should_play_after_expiry() -> None:
    ctx = DaemonContext()
    assert ctx.should_play("key1") is True
    # Manually expire the entry
    ctx._dedup["key1"] = time.monotonic() - 10.0  # pyright: ignore[reportPrivateUsage]
    assert ctx.should_play("key1") is True


# ---------------------------------------------------------------------------
# Session registry
# ---------------------------------------------------------------------------


def test_register_and_remove_session(tmp_path: Path) -> None:
    ctx = DaemonContext()
    config_path = tmp_path / ".vox" / "config.md"
    info = ctx.register_session("123", config_path)
    assert info.session_key == "123"
    assert "123" in ctx.sessions
    ctx.remove_session("123")
    assert "123" not in ctx.sessions


def test_remove_nonexistent_session() -> None:
    ctx = DaemonContext()
    # Should not raise
    ctx.remove_session("nonexistent")


# ---------------------------------------------------------------------------
# CWD resolution from PID
# ---------------------------------------------------------------------------


@patch("punt_vox.daemon.platform.system", return_value="Darwin")
@patch("punt_vox.daemon.subprocess.run")
def test_resolve_cwd_from_pid_macos(
    mock_run: MagicMock, _mock_platform: MagicMock
) -> None:
    mock_run.return_value = MagicMock(stdout="p42\nn/Users/test/project\n")
    result = resolve_cwd_from_pid(42)
    assert result == Path("/Users/test/project")


@patch("punt_vox.daemon.platform.system", return_value="Darwin")
@patch("punt_vox.daemon.subprocess.run")
def test_resolve_cwd_from_pid_macos_no_output(
    mock_run: MagicMock, _mock_platform: MagicMock
) -> None:
    mock_run.return_value = MagicMock(stdout="")
    result = resolve_cwd_from_pid(42)
    assert result is None


@patch("punt_vox.daemon.platform.system", return_value="Linux")
def test_resolve_cwd_from_pid_linux(_mock_platform: MagicMock, tmp_path: Path) -> None:
    # Can't easily test /proc, so test that it handles missing paths
    result = resolve_cwd_from_pid(999999)
    assert result is None


# ---------------------------------------------------------------------------
# Config resolution from session key
# ---------------------------------------------------------------------------


@patch("punt_vox.daemon.resolve_cwd_from_pid")
def test_resolve_config_from_session_key_found(
    mock_cwd: MagicMock, tmp_path: Path
) -> None:
    config_dir = tmp_path / ".vox"
    config_dir.mkdir()
    config_file = config_dir / "config.md"
    config_file.write_text('---\nnotify: "y"\n---\n')
    mock_cwd.return_value = tmp_path

    result = _resolve_config_from_session_key("42")
    assert result == config_file


@patch("punt_vox.daemon.resolve_cwd_from_pid", return_value=None)
def test_resolve_config_from_session_key_no_cwd(
    _mock_cwd: MagicMock,
) -> None:
    result = _resolve_config_from_session_key("42")
    assert result is None


def test_resolve_config_from_session_key_invalid() -> None:
    result = _resolve_config_from_session_key("not-a-pid")
    assert result is None


# ---------------------------------------------------------------------------
# Hook dispatch
# ---------------------------------------------------------------------------


@patch(
    "punt_vox.daemon.handle_stop",
    return_value={"decision": "block", "reason": "test"},
)
def test_dispatch_hook_stop(mock_stop: MagicMock, tmp_path: Path) -> None:
    config_path = tmp_path / ".vox" / "config.md"
    config_path.parent.mkdir(parents=True)
    config_path.write_text('---\nnotify: "y"\nspeak: "y"\n---\n')
    result = _dispatch_hook("Stop", {}, config_path)
    assert result is not None
    assert result["decision"] == "block"
    mock_stop.assert_called_once()


@patch("punt_vox.daemon.handle_post_bash")
def test_dispatch_hook_post_bash(mock_bash: MagicMock, tmp_path: Path) -> None:
    config_path = tmp_path / ".vox" / "config.md"
    config_path.parent.mkdir(parents=True)
    config_path.write_text('---\nnotify: "y"\n---\n')
    result = _dispatch_hook("PostToolUse", {}, config_path)
    assert result is None
    mock_bash.assert_called_once()


@patch("punt_vox.daemon.handle_notification")
def test_dispatch_hook_notification(mock_notif: MagicMock, tmp_path: Path) -> None:
    config_path = tmp_path / ".vox" / "config.md"
    config_path.parent.mkdir(parents=True)
    config_path.write_text('---\nnotify: "y"\n---\n')
    result = _dispatch_hook("Notification", {}, config_path)
    assert result is None
    mock_notif.assert_called_once()


def test_dispatch_hook_unknown(tmp_path: Path) -> None:
    config_path = tmp_path / ".vox" / "config.md"
    config_path.parent.mkdir(parents=True)
    config_path.write_text('---\nnotify: "y"\n---\n')
    result = _dispatch_hook("UnknownEvent", {}, config_path)
    assert result is None


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def test_build_app_returns_starlette() -> None:
    app = build_app()
    assert app is not None
    # Check routes exist
    route_paths = [getattr(r, "path", None) for r in app.routes]
    assert "/health" in route_paths
    assert "/mcp" in route_paths
    assert "/hook" in route_paths


def test_health_endpoint() -> None:
    ctx = DaemonContext()
    app = build_app(ctx)
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "uptime_seconds" in data
    assert data["active_sessions"] == 0
