"""Tests for punt_vox.service — daemon lifecycle management."""

from __future__ import annotations

import signal
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from punt_vox.service import (
    DEFAULT_PORT,
    _chown_to_user,  # pyright: ignore[reportPrivateUsage]
    _ensure_port_free,  # pyright: ignore[reportPrivateUsage]
    _ensure_user_dirs,  # pyright: ignore[reportPrivateUsage]
    _find_pid_on_port,  # pyright: ignore[reportPrivateUsage]
    _is_vox_daemon_process,  # pyright: ignore[reportPrivateUsage]
    _kill_pid,  # pyright: ignore[reportPrivateUsage]
    _kill_stale_daemon,  # pyright: ignore[reportPrivateUsage]
    _launchd_plist_content,  # pyright: ignore[reportPrivateUsage]
    _safe_systemd_value,  # pyright: ignore[reportPrivateUsage]
    _systemd_audio_env_lines,  # pyright: ignore[reportPrivateUsage]
    _systemd_unit_content,  # pyright: ignore[reportPrivateUsage]
    _user_keys_env_file_for,  # pyright: ignore[reportPrivateUsage]
    _voxd_exec_args,  # pyright: ignore[reportPrivateUsage]
    _write_keys_env,  # pyright: ignore[reportPrivateUsage]
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


def test_voxd_exec_args_resolves_relative_to_sys_executable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_voxd_exec_args must use sys.executable/../voxd, not $PATH."""
    # Create a fake distribution layout with our voxd.
    bin_dir = tmp_path / "fake-dist" / "bin"
    bin_dir.mkdir(parents=True)
    fake_voxd = bin_dir / "voxd"
    fake_voxd.write_text("#!/bin/sh\necho fake voxd")
    fake_voxd.chmod(0o755)
    fake_python = bin_dir / "python"
    fake_python.write_text('#!/bin/sh\nexec /usr/bin/python3 "$@"')
    fake_python.chmod(0o755)

    monkeypatch.setattr("punt_vox.service.sys.executable", str(fake_python))
    args = _voxd_exec_args()
    assert args[0] == str(fake_voxd)
    assert "--port" in args


def test_voxd_exec_args_ignores_stale_voxd_on_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale voxd earlier on $PATH must not be baked into ExecStart.

    This is the v3 install regression: shutil.which() picked up whichever
    voxd was first on PATH and could resolve to a stale binary from an
    earlier ``uv tool install`` that no longer matches the currently
    installed ``vox``.
    """
    # Fake "current" distribution
    current = tmp_path / "current" / "bin"
    current.mkdir(parents=True)
    (current / "voxd").write_text("current")
    (current / "voxd").chmod(0o755)
    (current / "python").write_text("#!/bin/sh\n")
    (current / "python").chmod(0o755)

    # Stale uv-tool binary — this is what shutil.which would have returned.
    stale_dir = tmp_path / "stale" / "bin"
    stale_dir.mkdir(parents=True)
    (stale_dir / "voxd").write_text("stale")
    (stale_dir / "voxd").chmod(0o755)

    monkeypatch.setenv("PATH", f"{stale_dir}:{current}:/usr/bin:/bin")
    monkeypatch.setattr("punt_vox.service.sys.executable", str(current / "python"))

    args = _voxd_exec_args()
    assert args[0] == str(current / "voxd"), (
        "Expected sys.executable-relative voxd, "
        f"got {args[0]} — stale binary from PATH leaked in."
    )


def test_voxd_exec_args_missing_binary_raises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing voxd next to sys.executable must raise SystemExit."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_python = bin_dir / "python"
    fake_python.write_text("#!/bin/sh\n")
    fake_python.chmod(0o755)
    # NOTE: no voxd in bin_dir

    monkeypatch.setattr("punt_vox.service.sys.executable", str(fake_python))
    with pytest.raises(SystemExit, match="voxd binary not found"):
        _voxd_exec_args()


# ---------------------------------------------------------------------------
# launchd plist content
# ---------------------------------------------------------------------------


@patch(
    "punt_vox.service._voxd_exec_args",
    return_value=["/usr/local/bin/voxd", "--port", "8421"],
)
def test_launchd_plist_contains_label(_mock_exec: MagicMock) -> None:
    content = _launchd_plist_content("testuser")
    assert "com.punt-labs.voxd" in content


@patch(
    "punt_vox.service._voxd_exec_args",
    return_value=["/usr/local/bin/voxd", "--port", "8421"],
)
def test_launchd_plist_contains_args(_mock_exec: MagicMock) -> None:
    content = _launchd_plist_content("testuser")
    assert "voxd" in content
    assert str(DEFAULT_PORT) in content


@patch(
    "punt_vox.service._voxd_exec_args",
    return_value=["/usr/local/bin/voxd", "--port", "8421"],
)
def test_launchd_plist_contains_log_paths(_mock_exec: MagicMock) -> None:
    content = _launchd_plist_content("testuser")
    assert "voxd-stdout.log" in content
    assert "voxd-stderr.log" in content


@patch(
    "punt_vox.service._voxd_exec_args",
    return_value=["/usr/local/bin/voxd", "--port", "8421"],
)
def test_launchd_plist_keepalive(_mock_exec: MagicMock) -> None:
    content = _launchd_plist_content("testuser")
    assert "<key>KeepAlive</key>" in content
    assert "<true/>" in content


@patch.dict("os.environ", {"PATH": "/opt/homebrew/bin:/usr/bin:/bin"})
@patch(
    "punt_vox.service._voxd_exec_args",
    return_value=["/opt/homebrew/bin/voxd", "--port", "8421"],
)
def test_launchd_plist_contains_path_from_env(_mock_exec: MagicMock) -> None:
    content = _launchd_plist_content("testuser")
    assert "<key>EnvironmentVariables</key>" in content
    assert "<key>PATH</key>" in content
    assert "/opt/homebrew/bin:/usr/bin:/bin" in content


# ---------------------------------------------------------------------------
# systemd unit content
# ---------------------------------------------------------------------------


@patch(
    "punt_vox.service._voxd_exec_args",
    return_value=["/usr/local/bin/voxd", "--port", "8421"],
)
def test_systemd_unit_contains_exec_start(_mock_exec: MagicMock) -> None:
    content = _systemd_unit_content("testuser")
    assert "ExecStart=" in content
    assert "voxd" in content
    assert str(DEFAULT_PORT) in content


@patch(
    "punt_vox.service._voxd_exec_args",
    return_value=["/usr/local/bin/voxd", "--port", "8421"],
)
def test_systemd_unit_restart_policy(_mock_exec: MagicMock) -> None:
    content = _systemd_unit_content("testuser")
    assert "Restart=on-failure" in content
    assert "RestartSec=5" in content


@patch.dict("os.environ", {"PATH": "/usr/local/bin:/usr/bin:/bin"})
@patch(
    "punt_vox.service._voxd_exec_args",
    return_value=["/usr/local/bin/voxd", "--port", "8421"],
)
def test_systemd_unit_contains_path_from_env(_mock_exec: MagicMock) -> None:
    content = _systemd_unit_content("testuser")
    assert 'Environment="PATH=/usr/local/bin:/usr/bin:/bin"' in content


@patch(
    "punt_vox.service._voxd_exec_args",
    return_value=["/usr/local/bin/voxd", "--port", "8421"],
)
def test_systemd_unit_description(_mock_exec: MagicMock) -> None:
    content = _systemd_unit_content("testuser")
    assert "Voxd text-to-speech daemon" in content


@patch(
    "punt_vox.service._voxd_exec_args",
    return_value=["/usr/local/bin/voxd", "--port", "8421"],
)
def test_systemd_unit_no_runtime_directory(_mock_exec: MagicMock) -> None:
    """RuntimeDirectory= must NOT be present — state lives in $HOME."""
    content = _systemd_unit_content("testuser")
    assert "RuntimeDirectory=" not in content
    assert "RuntimeDirectoryMode=" not in content


@patch(
    "punt_vox.service._voxd_exec_args",
    return_value=["/usr/local/bin/voxd", "--port", "8421"],
)
def test_systemd_unit_no_leading_whitespace(_mock_exec: MagicMock) -> None:
    """Section headers must start at column 0 — no leading spaces."""
    content = _systemd_unit_content("testuser")
    for line in content.splitlines():
        if line.startswith("["):
            assert line == line.lstrip(), (
                f"section header has leading whitespace: {line!r}"
            )


@patch.dict(
    "os.environ",
    {
        "PATH": "/usr/bin:/bin",
        "XDG_RUNTIME_DIR": "/run/user/1000",
        "PULSE_SERVER": "unix:/run/user/1000/pulse/native",
        "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
    },
)
@patch(
    "punt_vox.service._voxd_exec_args",
    return_value=["/usr/local/bin/voxd", "--port", "8421"],
)
def test_systemd_unit_includes_audio_env_vars(
    _mock_exec: MagicMock,
) -> None:
    """Audio env vars present in the environment appear in the unit file."""
    content = _systemd_unit_content("testuser")
    assert 'Environment="XDG_RUNTIME_DIR=/run/user/1000"' in content
    expected_pulse = 'Environment="PULSE_SERVER=unix:/run/user/1000/pulse/native"'
    assert expected_pulse in content
    expected_dbus = (
        'Environment="DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus"'
    )
    assert expected_dbus in content


@patch.dict(
    "os.environ",
    {
        "PATH": "/usr/bin:/bin",
        "XDG_RUNTIME_DIR": "/run/user/1000",
        "PULSE_SERVER": "unix:/run/user/1000/pulse/native",
    },
)
@patch(
    "punt_vox.service._voxd_exec_args",
    return_value=["/usr/local/bin/voxd", "--port", "8421"],
)
def test_systemd_unit_multiline_env_indented(
    _mock_exec: MagicMock,
) -> None:
    """Multiple Environment= lines each start at column 0 (after dedent)."""
    content = _systemd_unit_content("testuser")
    env_lines = [ln for ln in content.splitlines() if ln.startswith("Environment=")]
    assert len(env_lines) >= 2
    for line in env_lines:
        assert line == line.lstrip(), (
            f"Environment line has leading whitespace: {line!r}"
        )


@patch.dict("os.environ", {"PATH": "/usr/bin:/bin"}, clear=True)
@patch("punt_vox.service.pwd.getpwnam")
@patch(
    "punt_vox.service._voxd_exec_args",
    return_value=["/usr/local/bin/voxd", "--port", "8421"],
)
def test_systemd_unit_xdg_fallback_under_sudo(
    _mock_exec: MagicMock,
    mock_getpwnam: MagicMock,
) -> None:
    """When XDG_RUNTIME_DIR is absent, compute from target user UID."""
    mock_getpwnam.return_value = MagicMock(pw_uid=1000)
    content = _systemd_unit_content("deploy")
    assert 'Environment="XDG_RUNTIME_DIR=/run/user/1000"' in content
    assert "PULSE_SERVER" not in content
    assert "DBUS_SESSION_BUS_ADDRESS" not in content


@patch.dict(
    "os.environ",
    {"XDG_RUNTIME_DIR": "/run/user/1000"},
    clear=True,
)
def test_systemd_audio_env_lines_xdg_from_env() -> None:
    """XDG_RUNTIME_DIR from env is used directly — no pwd fallback."""
    lines = _systemd_audio_env_lines("testuser")
    assert len(lines) == 1
    assert 'Environment="XDG_RUNTIME_DIR=/run/user/1000"' in lines[0]


@patch.dict("os.environ", {}, clear=True)
@patch("punt_vox.service.pwd.getpwnam")
def test_systemd_audio_env_lines_xdg_fallback(
    mock_getpwnam: MagicMock,
) -> None:
    """No XDG_RUNTIME_DIR in env triggers pwd-based fallback."""
    mock_getpwnam.return_value = MagicMock(pw_uid=1000)
    lines = _systemd_audio_env_lines("deploy")
    assert len(lines) == 1
    assert lines[0] == 'Environment="XDG_RUNTIME_DIR=/run/user/1000"'
    mock_getpwnam.assert_called_once_with("deploy")


@patch.dict("os.environ", {}, clear=True)
@patch(
    "punt_vox.service.pwd.getpwnam",
    side_effect=KeyError("no such user"),
)
def test_systemd_audio_env_lines_fallback_unknown_user(
    _mock_getpwnam: MagicMock,
) -> None:
    """Unknown user produces empty list — no crash."""
    assert _systemd_audio_env_lines("ghost") == []


@patch.dict(
    "os.environ",
    {"XDG_RUNTIME_DIR": '/run/user/1000\nExecStartPre=/bin/evil"'},
    clear=True,
)
def test_systemd_audio_env_lines_rejects_unsafe_value() -> None:
    """Values with newlines or quotes are rejected."""
    lines = _systemd_audio_env_lines("testuser")
    # The unsafe XDG value is rejected; fallback fires from pwd.
    assert not any("evil" in line for line in lines)


def test_safe_systemd_value_accepts_normal() -> None:
    assert _safe_systemd_value("/run/user/1000") is True
    assert _safe_systemd_value("unix:path=/run/user/1000/bus") is True


def test_safe_systemd_value_rejects_newline() -> None:
    assert _safe_systemd_value("foo\nbar") is False


def test_safe_systemd_value_rejects_quote() -> None:
    assert _safe_systemd_value('foo"bar') is False


def test_safe_systemd_value_rejects_carriage_return() -> None:
    assert _safe_systemd_value("foo\rbar") is False


def test_safe_systemd_value_rejects_backslash() -> None:
    assert _safe_systemd_value("foo\\bar") is False


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
@patch("punt_vox.service._chown_to_user")
@patch("punt_vox.service._write_keys_env", return_value=Path("/fake/keys.env"))
@patch(
    "punt_vox.service._ensure_user_dirs",
    return_value=Path("/fake/home/.punt-labs/vox"),
)
@patch(
    "punt_vox.service._user_keys_env_file_for",
    return_value=Path("/fake/home/.punt-labs/vox/keys.env"),
)
@patch(
    "punt_vox.service._voxd_exec_args",
    return_value=["/usr/local/bin/voxd", "--port", "8421"],
)
@patch("punt_vox.service._installing_user", return_value="testuser")
@patch("punt_vox.service.detect_platform", return_value="macos")
def test_install_returns_running_status(
    _mock_platform: MagicMock,
    _mock_user: MagicMock,
    _mock_args: MagicMock,
    _mock_keys_path: MagicMock,
    _mock_dirs: MagicMock,
    _mock_keys: MagicMock,
    _mock_chown: MagicMock,
    _mock_launchd: MagicMock,
    _mock_status: MagicMock,
) -> None:
    """install() reports running status when launchd confirms the service."""
    result = install()
    assert "running" in result
    assert "voxd" in result


@patch("punt_vox.service._launchd_status", return_value=True)
@patch("punt_vox.service._launchd_install")
@patch("punt_vox.service._chown_to_user")
@patch("punt_vox.service._write_keys_env", return_value=Path("/fake/keys.env"))
@patch(
    "punt_vox.service._ensure_user_dirs",
    return_value=Path("/fake/home/.punt-labs/vox"),
)
@patch(
    "punt_vox.service._user_keys_env_file_for",
    return_value=Path("/fake/home/.punt-labs/vox/keys.env"),
)
@patch(
    "punt_vox.service._voxd_exec_args",
    return_value=["/usr/local/bin/voxd", "--port", "8421"],
)
@patch("punt_vox.service._installing_user", return_value="testuser")
@patch("punt_vox.service.detect_platform", return_value="macos")
def test_install_calls_ensure_user_dirs(
    _mock_platform: MagicMock,
    _mock_user: MagicMock,
    _mock_args: MagicMock,
    _mock_keys_path: MagicMock,
    mock_dirs: MagicMock,
    _mock_keys: MagicMock,
    _mock_chown: MagicMock,
    _mock_launchd: MagicMock,
    _mock_status: MagicMock,
) -> None:
    """install() creates per-user state directories via _ensure_user_dirs."""
    install()
    mock_dirs.assert_called_once_with("testuser")


@patch("punt_vox.service._launchd_status", return_value=True)
@patch("punt_vox.service._launchd_install")
@patch("punt_vox.service._chown_to_user")
@patch("punt_vox.service._write_keys_env", return_value=Path("/fake/keys.env"))
@patch(
    "punt_vox.service._ensure_user_dirs",
    return_value=Path("/fake/home/.punt-labs/vox"),
)
@patch(
    "punt_vox.service._user_keys_env_file_for",
    return_value=Path("/fake/home/.punt-labs/vox/keys.env"),
)
@patch(
    "punt_vox.service._voxd_exec_args",
    return_value=["/usr/local/bin/voxd", "--port", "8421"],
)
@patch("punt_vox.service._installing_user", return_value="testuser")
@patch("punt_vox.service.detect_platform", return_value="macos")
def test_install_calls_write_keys_env(
    _mock_platform: MagicMock,
    _mock_user: MagicMock,
    _mock_args: MagicMock,
    _mock_keys_path: MagicMock,
    _mock_dirs: MagicMock,
    mock_keys: MagicMock,
    _mock_chown: MagicMock,
    _mock_launchd: MagicMock,
    _mock_status: MagicMock,
) -> None:
    """install() writes provider keys to the user's keys.env path."""
    install()
    mock_keys.assert_called_once()
    # First arg is a dict (os.environ), second is keys_path (Path)
    args = mock_keys.call_args[0]
    assert isinstance(args[0], dict)
    assert isinstance(args[1], Path)
    # The second arg is the keys.env file itself, not a config dir.
    assert args[1].name == "keys.env"


@patch("punt_vox.service._launchd_status", return_value=True)
@patch("punt_vox.service._launchd_install")
@patch("punt_vox.service._chown_to_user")
@patch("punt_vox.service._write_keys_env", return_value=Path("/fake/keys.env"))
@patch(
    "punt_vox.service._ensure_user_dirs",
    return_value=Path("/fake/home/.punt-labs/vox"),
)
@patch(
    "punt_vox.service._user_keys_env_file_for",
    return_value=Path("/fake/home/.punt-labs/vox/keys.env"),
)
@patch(
    "punt_vox.service._voxd_exec_args",
    return_value=["/usr/local/bin/voxd", "--port", "8421"],
)
@patch("punt_vox.service._installing_user", return_value="testuser")
@patch("punt_vox.service.detect_platform", return_value="macos")
def test_install_chowns_keys_to_user(
    _mock_platform: MagicMock,
    _mock_user: MagicMock,
    _mock_args: MagicMock,
    _mock_keys_path: MagicMock,
    _mock_dirs: MagicMock,
    _mock_keys: MagicMock,
    mock_chown: MagicMock,
    _mock_launchd: MagicMock,
    _mock_status: MagicMock,
) -> None:
    """install() hands the resulting keys.env back to the installing user."""
    install()
    mock_chown.assert_called_once()
    args = mock_chown.call_args[0]
    assert isinstance(args[0], Path)
    assert args[1] == "testuser"


@patch("punt_vox.service._launchd_status", return_value=True)
@patch("punt_vox.service._launchd_install")
@patch("punt_vox.service._chown_to_user")
@patch("punt_vox.service._write_keys_env", return_value=Path("/fake/keys.env"))
@patch(
    "punt_vox.service._ensure_user_dirs",
    return_value=Path("/fake/home/.punt-labs/vox"),
)
@patch(
    "punt_vox.service._user_keys_env_file_for",
    return_value=Path("/fake/home/.punt-labs/vox/keys.env"),
)
@patch(
    "punt_vox.service._voxd_exec_args",
    return_value=["/usr/local/bin/voxd", "--port", "8421"],
)
@patch("punt_vox.service._installing_user", return_value="testuser")
@patch("punt_vox.service.detect_platform", return_value="macos")
def test_install_passes_user_to_launchd(
    _mock_platform: MagicMock,
    _mock_user: MagicMock,
    _mock_args: MagicMock,
    _mock_keys_path: MagicMock,
    _mock_dirs: MagicMock,
    _mock_keys: MagicMock,
    _mock_chown: MagicMock,
    mock_launchd: MagicMock,
    _mock_status: MagicMock,
) -> None:
    """install() passes the installing user to _launchd_install."""
    install()
    mock_launchd.assert_called_once_with("testuser")


@patch("punt_vox.service._launchd_status", return_value=False)
@patch("punt_vox.service._launchd_install")
@patch("punt_vox.service._chown_to_user")
@patch("punt_vox.service._write_keys_env", return_value=Path("/fake/keys.env"))
@patch(
    "punt_vox.service._ensure_user_dirs",
    return_value=Path("/fake/home/.punt-labs/vox"),
)
@patch(
    "punt_vox.service._user_keys_env_file_for",
    return_value=Path("/fake/home/.punt-labs/vox/keys.env"),
)
@patch(
    "punt_vox.service._voxd_exec_args",
    return_value=["/usr/local/bin/voxd", "--port", "8421"],
)
@patch("punt_vox.service._installing_user", return_value="testuser")
@patch("punt_vox.service.detect_platform", return_value="macos")
def test_install_reports_not_running(
    _mock_platform: MagicMock,
    _mock_user: MagicMock,
    _mock_args: MagicMock,
    _mock_keys_path: MagicMock,
    _mock_dirs: MagicMock,
    _mock_keys: MagicMock,
    _mock_chown: MagicMock,
    _mock_launchd: MagicMock,
    _mock_status: MagicMock,
) -> None:
    """install() reports 'not yet running' when launchd says service is down."""
    result = install()
    assert "not yet running" in result


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


# ---------------------------------------------------------------------------
# _write_keys_env — path and permissions
# ---------------------------------------------------------------------------


def test_write_keys_env_creates_file_at_target_path(tmp_path: Path) -> None:
    """_write_keys_env writes keys.env at the exact path the caller passed."""
    keys_path = tmp_path / "state" / "keys.env"
    env = {
        "ELEVENLABS_API_KEY": "sk-eleven-test",
        "TTS_PROVIDER": "elevenlabs",
    }
    result = _write_keys_env(env, keys_path)
    assert result == keys_path
    assert keys_path.exists()
    content = keys_path.read_text()
    assert "ELEVENLABS_API_KEY=sk-eleven-test" in content
    assert "TTS_PROVIDER=elevenlabs" in content


def test_write_keys_env_mode_0600(tmp_path: Path) -> None:
    """keys.env must always be chmod 0600 — it holds provider secrets."""
    import os as _os
    import stat as _stat

    keys_path = tmp_path / "keys.env"
    _write_keys_env({"OPENAI_API_KEY": "sk-test"}, keys_path)
    mode = _stat.S_IMODE(_os.stat(keys_path).st_mode)
    assert mode == 0o600, f"keys.env mode is {oct(mode)}, expected 0o600"


def test_write_keys_env_preserves_existing_keys(tmp_path: Path) -> None:
    """Keys already in the file are preserved when not overridden."""
    keys_path = tmp_path / "keys.env"
    keys_path.write_text(
        "# header\nELEVENLABS_API_KEY=original-eleven\nOPENAI_API_KEY=original-openai\n"
    )
    # Only pass one key; the other must remain.
    _write_keys_env({"OPENAI_API_KEY": "new-openai"}, keys_path)
    content = keys_path.read_text()
    assert "ELEVENLABS_API_KEY=original-eleven" in content
    assert "OPENAI_API_KEY=new-openai" in content


def test_write_keys_env_removes_empty_value_keys(tmp_path: Path) -> None:
    """An empty string in the env dict removes that key from the file."""
    keys_path = tmp_path / "keys.env"
    keys_path.write_text("ELEVENLABS_API_KEY=stale\n")
    _write_keys_env({"ELEVENLABS_API_KEY": ""}, keys_path)
    content = keys_path.read_text()
    assert "ELEVENLABS_API_KEY" not in content


def test_write_keys_env_no_sudo_required_note(tmp_path: Path) -> None:
    """Header must tell users they can edit without sudo."""
    keys_path = tmp_path / "keys.env"
    _write_keys_env({"TTS_PROVIDER": "say"}, keys_path)
    content = keys_path.read_text()
    assert "no sudo" in content.lower()


# ---------------------------------------------------------------------------
# _user_keys_env_file_for — routes to per-user state dir
# ---------------------------------------------------------------------------


@patch("punt_vox.paths.pwd.getpwnam")
def test_user_keys_env_file_for_routes_to_home(mock_getpwnam: MagicMock) -> None:
    """The keys.env path is computed from pwd.getpwnam(user).pw_dir."""
    mock_pw = MagicMock()
    mock_pw.pw_dir = "/home/jfreeman"
    mock_getpwnam.return_value = mock_pw
    assert _user_keys_env_file_for("jfreeman") == Path(
        "/home/jfreeman/.punt-labs/vox/keys.env"
    )


# ---------------------------------------------------------------------------
# _chown_to_user — runs only under root
# ---------------------------------------------------------------------------


@patch("punt_vox.service.os.chown")
@patch("punt_vox.service.os.getuid", return_value=1000)
def test_chown_to_user_noop_when_not_root(
    _mock_getuid: MagicMock, mock_chown: MagicMock
) -> None:
    """Non-root callers must never try to chown — would EPERM."""
    _chown_to_user(Path("/fake"), "jfreeman")
    mock_chown.assert_not_called()


@patch("punt_vox.service.os.chown")
@patch("punt_vox.service.pwd.getpwnam")
@patch("punt_vox.service.os.getuid", return_value=0)
def test_chown_to_user_chowns_when_root(
    _mock_getuid: MagicMock,
    mock_getpwnam: MagicMock,
    mock_chown: MagicMock,
) -> None:
    """Root callers chown the file to the target user."""
    mock_getpwnam.return_value = MagicMock(pw_uid=1000, pw_gid=1000)
    _chown_to_user(Path("/fake/keys.env"), "jfreeman")
    mock_chown.assert_called_once_with("/fake/keys.env", 1000, 1000)


@patch("punt_vox.service.os.chown")
@patch("punt_vox.service.pwd.getpwnam", side_effect=KeyError("unknown"))
@patch("punt_vox.service.os.getuid", return_value=0)
def test_chown_to_user_ignores_unknown_user(
    _mock_getuid: MagicMock,
    _mock_getpwnam: MagicMock,
    mock_chown: MagicMock,
) -> None:
    """Unknown user does not crash and skips chown."""
    _chown_to_user(Path("/fake"), "ghost")
    mock_chown.assert_not_called()


# ---------------------------------------------------------------------------
# install() under sudo: keys.env must end up owned by the installing user
# ---------------------------------------------------------------------------


@patch("punt_vox.service._launchd_status", return_value=True)
@patch("punt_vox.service._launchd_install")
@patch(
    "punt_vox.service._voxd_exec_args",
    return_value=["/usr/local/bin/voxd", "--port", "8421"],
)
@patch("punt_vox.service.detect_platform", return_value="macos")
def test_install_under_sudo_chowns_keys_env_to_sudo_user(
    _mock_platform: MagicMock,
    _mock_args: MagicMock,
    _mock_launchd: MagicMock,
    _mock_status: MagicMock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """sudo vox daemon install: resulting keys.env belongs to SUDO_USER.

    Regression test for the v3 install bug where _write_keys_env ran as
    root, left the file root-owned, and the daemon (running as the
    target user) could not read its own keys.
    """
    # Simulate sudo: SUDO_USER is set, and os.getuid() returns 0.
    monkeypatch.setenv("SUDO_USER", "jfreeman")

    def _fake_geteuid_zero() -> int:
        return 0

    monkeypatch.setattr("punt_vox.service.os.getuid", _fake_geteuid_zero)

    # Route home dir resolution to a fake home under tmp_path.
    fake_home = tmp_path / "home" / "jfreeman"
    fake_home.mkdir(parents=True)
    fake_pw = MagicMock(pw_uid=1000, pw_gid=1000, pw_dir=str(fake_home))

    def _fake_getpwnam(_user: str) -> MagicMock:
        return fake_pw

    monkeypatch.setattr("punt_vox.paths.pwd.getpwnam", _fake_getpwnam)
    monkeypatch.setattr("punt_vox.service.pwd.getpwnam", _fake_getpwnam)

    # Track chown calls so we can prove the file would have been chowned.
    chown_calls: list[tuple[str, int, int]] = []

    def _fake_chown(path: str, uid: int, gid: int) -> None:
        chown_calls.append((str(path), uid, gid))

    monkeypatch.setattr("punt_vox.service.os.chown", _fake_chown)

    install()

    expected_keys = fake_home / ".punt-labs" / "vox" / "keys.env"
    # _chown_to_user should have handed the keys.env back to jfreeman
    assert any(
        call_[0] == str(expected_keys) and call_[1] == 1000 and call_[2] == 1000
        for call_ in chown_calls
    ), (
        f"keys.env at {expected_keys} was not chowned to jfreeman; "
        f"chown calls: {chown_calls}"
    )


# ---------------------------------------------------------------------------
# _ensure_user_dirs — end-to-end with tmp_path
# ---------------------------------------------------------------------------


def test_ensure_user_dirs_creates_all_subdirs_under_target_user_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: calling _ensure_user_dirs creates the tree and returns root."""
    fake_home = tmp_path / "home" / "testuser"
    fake_home.mkdir(parents=True)
    fake_pw = MagicMock(pw_uid=1000, pw_gid=1000, pw_dir=str(fake_home))

    def _fake_getpwnam(_user: str) -> MagicMock:
        return fake_pw

    def _fake_geteuid_nonroot() -> int:
        return 1000

    monkeypatch.setattr("punt_vox.paths.pwd.getpwnam", _fake_getpwnam)
    # Not root — no chown path.
    monkeypatch.setattr("punt_vox.service.os.getuid", _fake_geteuid_nonroot)

    result = _ensure_user_dirs("testuser")
    expected = fake_home / ".punt-labs" / "vox"
    assert result == expected
    assert expected.is_dir()
    assert (expected / "logs").is_dir()
    assert (expected / "run").is_dir()
    assert (expected / "cache").is_dir()
