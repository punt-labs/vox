"""Tests for punt_vox.service — daemon lifecycle management."""

from __future__ import annotations

import signal
import subprocess
import sys
from unittest.mock import MagicMock, call, patch

import pytest

from punt_vox.daemon import DEFAULT_PORT
from punt_vox.service import (
    _find_pid_on_port,  # pyright: ignore[reportPrivateUsage]
    _is_vox_daemon_process,  # pyright: ignore[reportPrivateUsage]
    _kill_pid,  # pyright: ignore[reportPrivateUsage]
    _kill_stale_daemon,  # pyright: ignore[reportPrivateUsage]
    _launchd_plist_content,  # pyright: ignore[reportPrivateUsage]
    _systemd_unit_content,  # pyright: ignore[reportPrivateUsage]
    _vox_exec_args,  # pyright: ignore[reportPrivateUsage]
    detect_platform,
)

# ---------------------------------------------------------------------------
# Exec args
# ---------------------------------------------------------------------------


def test_vox_exec_args() -> None:
    args = _vox_exec_args()
    assert args[0] == sys.executable
    assert "-m" in args
    assert "punt_vox" in args
    assert "serve" in args
    assert "--port" in args
    assert str(DEFAULT_PORT) in args


# ---------------------------------------------------------------------------
# launchd plist content
# ---------------------------------------------------------------------------


def test_launchd_plist_contains_label() -> None:
    content = _launchd_plist_content()
    assert "com.punt-labs.vox" in content


def test_launchd_plist_contains_args() -> None:
    content = _launchd_plist_content()
    assert "serve" in content
    assert str(DEFAULT_PORT) in content


def test_launchd_plist_contains_log_paths() -> None:
    content = _launchd_plist_content()
    assert "daemon-stdout.log" in content
    assert "daemon-stderr.log" in content


def test_launchd_plist_keepalive() -> None:
    content = _launchd_plist_content()
    assert "<key>KeepAlive</key>" in content
    assert "<true/>" in content


@patch.dict(
    "os.environ",
    {"PATH": "/opt/homebrew/bin:/usr/bin:/bin", "ELEVENLABS_API_KEY": "sk-test-123"},
    clear=True,
)
def test_launchd_plist_contains_env_from_capture() -> None:
    content = _launchd_plist_content()
    assert "<key>EnvironmentVariables</key>" in content
    assert "<key>PATH</key>" in content
    assert "/opt/homebrew/bin:/usr/bin:/bin" in content
    assert "<key>ELEVENLABS_API_KEY</key>" in content
    assert "sk-test-123" in content


# ---------------------------------------------------------------------------
# systemd unit content
# ---------------------------------------------------------------------------


def test_systemd_unit_contains_exec_start() -> None:
    content = _systemd_unit_content()
    assert "ExecStart=" in content
    assert "serve" in content
    assert str(DEFAULT_PORT) in content


def test_systemd_unit_restart_policy() -> None:
    content = _systemd_unit_content()
    assert "Restart=on-failure" in content
    assert "RestartSec=5" in content


@patch.dict(
    "os.environ",
    {"PATH": "/usr/local/bin:/usr/bin:/bin", "OPENAI_API_KEY": "sk-openai-test"},
    clear=True,
)
def test_systemd_unit_contains_env_from_capture() -> None:
    content = _systemd_unit_content()
    assert 'Environment="PATH=/usr/local/bin:/usr/bin:/bin"' in content
    assert 'Environment="OPENAI_API_KEY=sk-openai-test"' in content


def test_systemd_unit_description() -> None:
    content = _systemd_unit_content()
    assert "Vox text-to-speech daemon" in content


# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------


@patch("punt_vox.service.platform.system", return_value="Darwin")
def test_detect_platform_macos(_mock: MagicMock) -> None:
    assert detect_platform() == "macos"


@patch("punt_vox.service.platform.system", return_value="Linux")
def test_detect_platform_linux(_mock: MagicMock) -> None:
    assert detect_platform() == "linux"


@patch("punt_vox.service.platform.system", return_value="Windows")
def test_detect_platform_unsupported(_mock: MagicMock) -> None:
    with pytest.raises(SystemExit):
        detect_platform()


# ---------------------------------------------------------------------------
# _find_pid_on_port
# ---------------------------------------------------------------------------


@patch("punt_vox.service.platform.system", return_value="Darwin")
@patch("punt_vox.service.subprocess.run")
def test_find_pid_on_port_macos(mock_run: MagicMock, _mock_sys: MagicMock) -> None:
    mock_run.return_value = MagicMock(returncode=0, stdout="12345\n")
    assert _find_pid_on_port(8421) == [12345]
    mock_run.assert_called_once_with(
        ["lsof", "-ti", ":8421"], capture_output=True, text=True, timeout=5
    )


@patch("punt_vox.service.platform.system", return_value="Darwin")
@patch("punt_vox.service.subprocess.run")
def test_find_pid_on_port_macos_multiple(
    mock_run: MagicMock, _mock_sys: MagicMock
) -> None:
    # lsof returns one PID per line — daemon + mcp-proxy client.
    mock_run.return_value = MagicMock(returncode=0, stdout="12345\n67890\n")
    assert _find_pid_on_port(8421) == [12345, 67890]


@patch("punt_vox.service.platform.system", return_value="Linux")
@patch("punt_vox.service.subprocess.run")
def test_find_pid_on_port_linux(mock_run: MagicMock, _mock_sys: MagicMock) -> None:
    # fuser returns "8421/tcp:  6789" — not a bare PID.
    mock_run.return_value = MagicMock(returncode=0, stdout="8421/tcp:  6789\n")
    assert _find_pid_on_port(8421) == [6789]
    mock_run.assert_called_once_with(
        ["fuser", "8421/tcp"], capture_output=True, text=True, timeout=5
    )


@patch("punt_vox.service.platform.system", return_value="Darwin")
@patch("punt_vox.service.subprocess.run")
def test_find_pid_on_port_empty_when_not_bound(
    mock_run: MagicMock, _mock_sys: MagicMock
) -> None:
    mock_run.return_value = MagicMock(returncode=1, stdout="")
    assert _find_pid_on_port(8421) == []


@patch("punt_vox.service.platform.system", return_value="Darwin")
@patch(
    "punt_vox.service.subprocess.run",
    side_effect=subprocess.TimeoutExpired(cmd="lsof", timeout=5),
)
def test_find_pid_on_port_timeout(_mock_run: MagicMock, _mock_sys: MagicMock) -> None:
    assert _find_pid_on_port(8421) == []


# ---------------------------------------------------------------------------
# _kill_pid
# ---------------------------------------------------------------------------


@patch("punt_vox.service.os.kill")
def test_kill_pid_exits_after_sigterm(mock_kill: MagicMock) -> None:
    # First call: SIGTERM succeeds. Second call (probe): process gone.
    mock_kill.side_effect = [None, ProcessLookupError]
    assert _kill_pid(100) is True
    assert mock_kill.call_args_list[0] == call(100, signal.SIGTERM)
    assert mock_kill.call_args_list[1] == call(100, 0)


@patch("punt_vox.service.os.kill")
def test_kill_pid_already_gone(mock_kill: MagicMock) -> None:
    mock_kill.side_effect = ProcessLookupError
    assert _kill_pid(100) is True
    mock_kill.assert_called_once_with(100, signal.SIGTERM)


# ---------------------------------------------------------------------------
# _kill_stale_daemon
# ---------------------------------------------------------------------------


@patch("punt_vox.service._remove_port_file")
@patch("punt_vox.service._kill_pid", return_value=True)
@patch("punt_vox.service._is_vox_daemon_process", return_value=True)
@patch("punt_vox.service._find_pid_on_port", return_value=[999])
@patch("punt_vox.service.read_port_file", return_value=8421)
def test_kill_stale_daemon_kills_process(
    _mock_port: MagicMock,
    mock_find: MagicMock,
    _mock_is_vox: MagicMock,
    mock_kill: MagicMock,
    mock_remove: MagicMock,
) -> None:
    assert _kill_stale_daemon() is True
    mock_find.assert_called_once_with(8421)
    mock_kill.assert_called_once_with(999)
    mock_remove.assert_called_once()


@patch("punt_vox.service._find_pid_on_port", return_value=[])
@patch("punt_vox.service.read_port_file", return_value=None)
def test_kill_stale_daemon_no_process(
    _mock_port: MagicMock, _mock_find: MagicMock
) -> None:
    assert _kill_stale_daemon() is False


@patch("punt_vox.service._remove_port_file")
@patch("punt_vox.service._kill_pid", return_value=True)
@patch("punt_vox.service._is_vox_daemon_process", return_value=True)
@patch("punt_vox.service._find_pid_on_port", return_value=[555])
@patch("punt_vox.service.read_port_file", return_value=None)
def test_kill_stale_daemon_uses_default_port(
    _mock_port: MagicMock,
    mock_find: MagicMock,
    _mock_is_vox: MagicMock,
    mock_kill: MagicMock,
    _mock_remove: MagicMock,
) -> None:
    assert _kill_stale_daemon() is True
    mock_find.assert_called_once_with(DEFAULT_PORT)


@patch("punt_vox.service._kill_pid")
@patch("punt_vox.service._is_vox_daemon_process", return_value=False)
@patch("punt_vox.service._find_pid_on_port", return_value=[999])
@patch("punt_vox.service.read_port_file", return_value=8421)
def test_kill_stale_daemon_skips_non_vox_process(
    _mock_port: MagicMock,
    _mock_find: MagicMock,
    _mock_is_vox: MagicMock,
    mock_kill: MagicMock,
) -> None:
    assert _kill_stale_daemon() is False
    mock_kill.assert_not_called()


@patch("punt_vox.service._remove_port_file")
@patch("punt_vox.service._kill_pid", return_value=True)
@patch("punt_vox.service._is_vox_daemon_process", side_effect=[False, True])
@patch("punt_vox.service._find_pid_on_port", return_value=[100, 200])
@patch("punt_vox.service.read_port_file", return_value=8421)
def test_kill_stale_daemon_iterates_pids(
    _mock_port: MagicMock,
    _mock_find: MagicMock,
    mock_is_vox: MagicMock,
    mock_kill: MagicMock,
    mock_remove: MagicMock,
) -> None:
    """First PID is a client (not vox), second is the daemon — kills second."""
    assert _kill_stale_daemon() is True
    assert mock_is_vox.call_count == 2
    mock_kill.assert_called_once_with(200)
    mock_remove.assert_called_once()


@patch("punt_vox.service._remove_port_file")
@patch("punt_vox.service._kill_pid", return_value=False)
@patch("punt_vox.service._is_vox_daemon_process", return_value=True)
@patch("punt_vox.service._find_pid_on_port", return_value=[999])
@patch("punt_vox.service.read_port_file", return_value=8421)
def test_kill_stale_daemon_no_cleanup_on_kill_failure(
    _mock_port: MagicMock,
    _mock_find: MagicMock,
    _mock_is_vox: MagicMock,
    mock_kill: MagicMock,
    mock_remove: MagicMock,
) -> None:
    """When _kill_pid fails, state files are NOT removed."""
    assert _kill_stale_daemon() is False
    mock_kill.assert_called_once_with(999)
    mock_remove.assert_not_called()


# ---------------------------------------------------------------------------
# _is_vox_daemon_process
# ---------------------------------------------------------------------------


@patch("punt_vox.service.subprocess.run")
def test_is_vox_daemon_process_true(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(
        stdout="/usr/bin/python3 -m punt_vox serve --port 8421"
    )
    assert _is_vox_daemon_process(123) is True


@patch("punt_vox.service.subprocess.run")
def test_is_vox_daemon_process_hyphen_path(mock_run: MagicMock) -> None:
    """Matches when cmd contains punt-vox (hyphen) but not punt_vox."""
    cmd = "/home/user/.local/share/uv/tools/punt-vox/bin/vox serve --port 8421"
    mock_run.return_value = MagicMock(stdout=cmd)
    assert _is_vox_daemon_process(123) is True


@patch("punt_vox.service.subprocess.run")
def test_is_vox_daemon_process_bare_vox_binary(mock_run: MagicMock) -> None:
    """Matches when started as bare ``vox serve`` without punt_vox in path."""
    mock_run.return_value = MagicMock(
        stdout="/Users/jfreeman/.local/bin/vox serve --port 8421"
    )
    assert _is_vox_daemon_process(123) is True


@patch("punt_vox.service.subprocess.run")
def test_is_vox_daemon_process_false(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(stdout="nginx: master process")
    assert _is_vox_daemon_process(123) is False


@patch(
    "punt_vox.service.subprocess.run",
    side_effect=subprocess.TimeoutExpired(cmd="ps", timeout=5),
)
def test_is_vox_daemon_process_timeout(_mock_run: MagicMock) -> None:
    assert _is_vox_daemon_process(123) is False


# ---------------------------------------------------------------------------
# _kill_pid — PermissionError
# ---------------------------------------------------------------------------


@patch("punt_vox.service.os.kill", side_effect=PermissionError)
def test_kill_pid_permission_error(mock_kill: MagicMock) -> None:
    assert _kill_pid(100) is False
    mock_kill.assert_called_once_with(100, signal.SIGTERM)


# ---------------------------------------------------------------------------
# _kill_pid — SIGKILL fallback
# ---------------------------------------------------------------------------


@patch("punt_vox.service.time.sleep")
@patch("punt_vox.service.time.monotonic")
@patch("punt_vox.service.os.kill")
def test_kill_pid_sigkill_after_timeout(
    mock_kill: MagicMock, mock_monotonic: MagicMock, _mock_sleep: MagicMock
) -> None:
    # SIGTERM succeeds, probes never raise (process alive), then SIGKILL,
    # then post-SIGKILL probe raises ProcessLookupError (confirmed dead).
    mock_kill.side_effect = [None, None, None, ProcessLookupError]
    # monotonic: SIGTERM deadline (0.0), probe before deadline (0.0),
    # past deadline (6.0), post-SIGKILL deadline (6.0), probe check (6.0).
    mock_monotonic.side_effect = [0.0, 0.0, 6.0, 6.0, 6.0]
    assert _kill_pid(100) is True
    assert mock_kill.call_args_list == [
        call(100, signal.SIGTERM),
        call(100, 0),
        call(100, signal.SIGKILL),
        call(100, 0),
    ]
