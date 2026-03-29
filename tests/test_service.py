"""Tests for punt_vox.service — daemon lifecycle management."""

from __future__ import annotations

import signal
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from punt_vox.service import (
    DEFAULT_PORT,
    _ensure_port_free,  # pyright: ignore[reportPrivateUsage]
    _find_pid_on_port,  # pyright: ignore[reportPrivateUsage]
    _is_vox_daemon_process,  # pyright: ignore[reportPrivateUsage]
    _kill_pid,  # pyright: ignore[reportPrivateUsage]
    _kill_stale_daemon,  # pyright: ignore[reportPrivateUsage]
    _launchd_plist_content,  # pyright: ignore[reportPrivateUsage]
    _systemd_unit_content,  # pyright: ignore[reportPrivateUsage]
    _voxd_exec_args,  # pyright: ignore[reportPrivateUsage]
    detect_platform,
    install,
)

# ---------------------------------------------------------------------------
# Exec args
# ---------------------------------------------------------------------------


def test_voxd_exec_args() -> None:
    args = _voxd_exec_args()
    assert args[0].endswith("voxd")
    assert "--port" in args
    assert str(DEFAULT_PORT) in args


# ---------------------------------------------------------------------------
# launchd plist content
# ---------------------------------------------------------------------------


@patch("punt_vox.service.shutil.which", return_value="/usr/local/bin/voxd")
def test_launchd_plist_contains_label(_mock_which: MagicMock) -> None:
    content = _launchd_plist_content("testuser")
    assert "com.punt-labs.voxd" in content


@patch("punt_vox.service.shutil.which", return_value="/usr/local/bin/voxd")
def test_launchd_plist_contains_args(_mock_which: MagicMock) -> None:
    content = _launchd_plist_content("testuser")
    assert "voxd" in content
    assert str(DEFAULT_PORT) in content


@patch("punt_vox.service.shutil.which", return_value="/usr/local/bin/voxd")
def test_launchd_plist_contains_log_paths(_mock_which: MagicMock) -> None:
    content = _launchd_plist_content("testuser")
    assert "voxd-stdout.log" in content
    assert "voxd-stderr.log" in content


@patch("punt_vox.service.shutil.which", return_value="/usr/local/bin/voxd")
def test_launchd_plist_keepalive(_mock_which: MagicMock) -> None:
    content = _launchd_plist_content("testuser")
    assert "<key>KeepAlive</key>" in content
    assert "<true/>" in content


@patch.dict("os.environ", {"PATH": "/opt/homebrew/bin:/usr/bin:/bin"})
@patch("punt_vox.service.shutil.which", return_value="/opt/homebrew/bin/voxd")
def test_launchd_plist_contains_path_from_env(_mock_which: MagicMock) -> None:
    content = _launchd_plist_content("testuser")
    assert "<key>EnvironmentVariables</key>" in content
    assert "<key>PATH</key>" in content
    assert "/opt/homebrew/bin:/usr/bin:/bin" in content


# ---------------------------------------------------------------------------
# systemd unit content
# ---------------------------------------------------------------------------


@patch("punt_vox.service.shutil.which", return_value="/usr/local/bin/voxd")
def test_systemd_unit_contains_exec_start(_mock_which: MagicMock) -> None:
    content = _systemd_unit_content("testuser")
    assert "ExecStart=" in content
    assert "voxd" in content
    assert str(DEFAULT_PORT) in content


@patch("punt_vox.service.shutil.which", return_value="/usr/local/bin/voxd")
def test_systemd_unit_restart_policy(_mock_which: MagicMock) -> None:
    content = _systemd_unit_content("testuser")
    assert "Restart=on-failure" in content
    assert "RestartSec=5" in content


@patch.dict("os.environ", {"PATH": "/usr/local/bin:/usr/bin:/bin"})
@patch("punt_vox.service.shutil.which", return_value="/usr/local/bin/voxd")
def test_systemd_unit_contains_path_from_env(_mock_which: MagicMock) -> None:
    content = _systemd_unit_content("testuser")
    assert 'Environment="PATH=/usr/local/bin:/usr/bin:/bin"' in content


@patch("punt_vox.service.shutil.which", return_value="/usr/local/bin/voxd")
def test_systemd_unit_description(_mock_which: MagicMock) -> None:
    content = _systemd_unit_content("testuser")
    assert "Voxd text-to-speech daemon" in content


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


# ---------------------------------------------------------------------------
# _launchd_install — subprocess sequence
# ---------------------------------------------------------------------------


@patch("punt_vox.service.subprocess.run")
@patch("punt_vox.service._ensure_port_free")
@patch("punt_vox.service._LAUNCHD_PLIST")
@patch("punt_vox.service._launchd_plist_content", return_value="<plist>test</plist>")
def test_launchd_install_fresh(
    _mock_content: MagicMock,
    mock_plist: MagicMock,
    mock_ensure: MagicMock,
    mock_run: MagicMock,
) -> None:
    """Fresh install: no unload, ensure_port_free, write plist, load."""
    from punt_vox.service import _launchd_install  # pyright: ignore[reportPrivateUsage]

    mock_plist.exists.return_value = False
    mock_run.return_value = MagicMock(returncode=0)

    _launchd_install("testuser")

    # No unload call -- plist didn't exist
    mock_ensure.assert_called_once()
    mock_plist.write_text.assert_called_once_with("<plist>test</plist>")
    # Only the load call
    assert mock_run.call_count == 1
    load_call = mock_run.call_args_list[0]
    assert "load" in load_call[0][0]
    assert "-w" in load_call[0][0]


@patch("punt_vox.service.subprocess.run")
@patch("punt_vox.service._ensure_port_free")
@patch("punt_vox.service._LAUNCHD_PLIST")
@patch("punt_vox.service._launchd_plist_content", return_value="<plist>test</plist>")
def test_launchd_install_upgrade(
    _mock_content: MagicMock,
    mock_plist: MagicMock,
    mock_ensure: MagicMock,
    mock_run: MagicMock,
) -> None:
    """Upgrade: unload existing, ensure_port_free, write plist, load."""
    from punt_vox.service import _launchd_install  # pyright: ignore[reportPrivateUsage]

    mock_plist.exists.return_value = True
    mock_run.return_value = MagicMock(returncode=0)

    _launchd_install("testuser")

    # First call is unload, second is load
    assert mock_run.call_count == 2
    unload_call = mock_run.call_args_list[0]
    assert "unload" in unload_call[0][0]
    assert "-w" in unload_call[0][0]

    mock_ensure.assert_called_once()
    mock_plist.write_text.assert_called_once_with("<plist>test</plist>")

    load_call = mock_run.call_args_list[1]
    assert "load" in load_call[0][0]


# ---------------------------------------------------------------------------
# _systemd_install — subprocess sequence
# ---------------------------------------------------------------------------


@patch("punt_vox.service.subprocess.run")
@patch("punt_vox.service._ensure_port_free")
@patch("punt_vox.service._SYSTEMD_UNIT")
@patch("punt_vox.service._SYSTEMD_DIR")
@patch("punt_vox.service._systemd_unit_content", return_value="[Unit]\ntest")
def test_systemd_install_fresh(
    _mock_content: MagicMock,
    mock_systemd_dir: MagicMock,
    mock_unit: MagicMock,
    mock_ensure: MagicMock,
    mock_run: MagicMock,
) -> None:
    """Fresh install: no stop, ensure_port_free, write unit, reload, enable."""
    from punt_vox.service import _systemd_install  # pyright: ignore[reportPrivateUsage]

    mock_unit.exists.return_value = False
    mock_run.return_value = MagicMock(returncode=0)

    _systemd_install("testuser")

    # No stop call -- unit didn't exist
    mock_ensure.assert_called_once()
    mock_unit.write_text.assert_called_once_with("[Unit]\ntest")
    # daemon-reload + enable --now
    assert mock_run.call_count == 2
    reload_call = mock_run.call_args_list[0]
    assert "daemon-reload" in reload_call[0][0]
    enable_call = mock_run.call_args_list[1]
    assert "enable" in enable_call[0][0]
    assert "--now" in enable_call[0][0]


@patch("punt_vox.service.subprocess.run")
@patch("punt_vox.service._ensure_port_free")
@patch("punt_vox.service._SYSTEMD_UNIT")
@patch("punt_vox.service._SYSTEMD_DIR")
@patch("punt_vox.service._systemd_unit_content", return_value="[Unit]\ntest")
def test_systemd_install_upgrade(
    _mock_content: MagicMock,
    mock_systemd_dir: MagicMock,
    mock_unit: MagicMock,
    mock_ensure: MagicMock,
    mock_run: MagicMock,
) -> None:
    """Upgrade: stop existing, ensure_port_free, write unit, reload, enable."""
    from punt_vox.service import _systemd_install  # pyright: ignore[reportPrivateUsage]

    mock_unit.exists.return_value = True
    mock_run.return_value = MagicMock(returncode=0)

    _systemd_install("testuser")

    # First call is stop, then daemon-reload, then enable --now
    assert mock_run.call_count == 3
    stop_call = mock_run.call_args_list[0]
    assert "stop" in stop_call[0][0]
    assert "voxd" in stop_call[0][0]

    mock_ensure.assert_called_once()
    mock_unit.write_text.assert_called_once_with("[Unit]\ntest")

    reload_call = mock_run.call_args_list[1]
    assert "daemon-reload" in reload_call[0][0]
    enable_call = mock_run.call_args_list[2]
    assert "enable" in enable_call[0][0]


# ---------------------------------------------------------------------------
# install() — public API
# ---------------------------------------------------------------------------


@patch("punt_vox.service._launchd_status", return_value=True)
@patch("punt_vox.service._launchd_install")
@patch(
    "punt_vox.service._vox_exec_args",
    return_value=["/usr/bin/vox", "serve", "--port", "8421"],
)
@patch("punt_vox.service.detect_platform", return_value="macos")
@patch("punt_vox.service.write_keys_env", return_value=Path("/fake/keys.env"))
def test_install_reuses_existing_token(
    mock_keys: MagicMock,
    _mock_platform: MagicMock,
    _mock_args: MagicMock,
    _mock_launchd: MagicMock,
    _mock_status: MagicMock,
    tmp_path: Path,
) -> None:
    """install() reuses existing token when serve.token exists and is non-empty."""
    data_dir = tmp_path / "vox-data"
    data_dir.mkdir(parents=True)
    token_path = data_dir / "serve.token"
    token_path.write_text("existing-secret-token")

    with patch("punt_vox.service.VOX_DATA_DIR", data_dir):
        result = install()

    mock_keys.assert_called_once()
    # Token file should still contain original value — not overwritten
    assert token_path.read_text() == "existing-secret-token"
    assert "running" in result


@patch("punt_vox.service._launchd_status", return_value=True)
@patch("punt_vox.service._launchd_install")
@patch(
    "punt_vox.service._vox_exec_args",
    return_value=["/usr/bin/vox", "serve", "--port", "8421"],
)
@patch("punt_vox.service.detect_platform", return_value="macos")
@patch("punt_vox.service.write_keys_env", return_value=Path("/fake/keys.env"))
def test_install_generates_token_when_missing(
    mock_keys: MagicMock,
    _mock_platform: MagicMock,
    _mock_args: MagicMock,
    _mock_launchd: MagicMock,
    _mock_status: MagicMock,
    tmp_path: Path,
) -> None:
    """install() generates new token when serve.token does not exist."""
    data_dir = tmp_path / "vox-data"
    data_dir.mkdir(parents=True)
    token_path = data_dir / "serve.token"

    with patch("punt_vox.service.VOX_DATA_DIR", data_dir):
        install()

    assert token_path.exists()
    assert len(token_path.read_text().strip()) > 0


@patch("punt_vox.service._launchd_status", return_value=True)
@patch("punt_vox.service._launchd_install")
@patch(
    "punt_vox.service._vox_exec_args",
    return_value=["/usr/bin/vox", "serve", "--port", "8421"],
)
@patch("punt_vox.service.detect_platform", return_value="macos")
@patch("punt_vox.service.write_keys_env", return_value=Path("/fake/keys.env"))
def test_install_generates_token_when_empty(
    mock_keys: MagicMock,
    _mock_platform: MagicMock,
    _mock_args: MagicMock,
    _mock_launchd: MagicMock,
    _mock_status: MagicMock,
    tmp_path: Path,
) -> None:
    """install() generates new token when serve.token exists but is empty."""
    data_dir = tmp_path / "vox-data"
    data_dir.mkdir(parents=True)
    token_path = data_dir / "serve.token"
    token_path.write_text("")

    with patch("punt_vox.service.VOX_DATA_DIR", data_dir):
        install()

    assert len(token_path.read_text().strip()) > 0


@patch("punt_vox.service._launchd_status", return_value=True)
@patch("punt_vox.service._launchd_install")
@patch(
    "punt_vox.service._vox_exec_args",
    return_value=["/usr/bin/vox", "serve", "--port", "8421"],
)
@patch("punt_vox.service.detect_platform", return_value="macos")
@patch("punt_vox.service.write_keys_env", return_value=Path("/fake/keys.env"))
def test_install_creates_parent_dir(
    mock_keys: MagicMock,
    _mock_platform: MagicMock,
    _mock_args: MagicMock,
    _mock_launchd: MagicMock,
    _mock_status: MagicMock,
    tmp_path: Path,
) -> None:
    """install() creates VOX_DATA_DIR before writing token."""
    data_dir = tmp_path / "nonexistent" / "vox-data"
    # Parent does not exist yet

    with patch("punt_vox.service.VOX_DATA_DIR", data_dir):
        install()

    assert data_dir.exists()
    token_path = data_dir / "serve.token"
    assert token_path.exists()


@patch("punt_vox.service._launchd_status", return_value=True)
@patch("punt_vox.service._launchd_install")
@patch(
    "punt_vox.service._vox_exec_args",
    return_value=["/usr/bin/vox", "serve", "--port", "8421"],
)
@patch("punt_vox.service.detect_platform", return_value="macos")
@patch("punt_vox.service.write_keys_env", return_value=Path("/fake/keys.env"))
def test_install_calls_write_keys_env(
    mock_keys: MagicMock,
    _mock_platform: MagicMock,
    _mock_args: MagicMock,
    _mock_launchd: MagicMock,
    _mock_status: MagicMock,
    tmp_path: Path,
) -> None:
    """install() calls write_keys_env with the environment."""
    data_dir = tmp_path / "vox-data"
    data_dir.mkdir(parents=True)

    with patch("punt_vox.service.VOX_DATA_DIR", data_dir):
        install()

    mock_keys.assert_called_once()
    # Argument should be a dict (os.environ)
    args = mock_keys.call_args[0]
    assert isinstance(args[0], dict)


# ---------------------------------------------------------------------------
# _ensure_port_free — SystemExit when port occupied
# ---------------------------------------------------------------------------


@patch("punt_vox.service._find_pid_on_port", return_value=[1234])
@patch("punt_vox.service._kill_stale_daemon", return_value=False)
def test_ensure_port_free_raises_when_occupied(
    _mock_kill: MagicMock,
    _mock_find: MagicMock,
) -> None:
    """_ensure_port_free raises SystemExit when port is still occupied after kill."""
    with pytest.raises(SystemExit, match="still in use"):
        _ensure_port_free()


@patch("punt_vox.service._find_pid_on_port", return_value=[])
@patch("punt_vox.service._kill_stale_daemon", return_value=False)
def test_ensure_port_free_succeeds_when_clear(
    _mock_kill: MagicMock,
    _mock_find: MagicMock,
) -> None:
    """_ensure_port_free succeeds when no PIDs on port after kill."""
    _ensure_port_free()  # Should not raise
