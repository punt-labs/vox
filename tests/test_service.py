"""Tests for punt_vox.service — daemon lifecycle management."""

from __future__ import annotations

import os
import signal
import stat as stat_mod
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from punt_vox.service import (
    DEFAULT_PORT,
    _ensure_port_free,  # pyright: ignore[reportPrivateUsage]
    _ensure_user_dirs,  # pyright: ignore[reportPrivateUsage]
    _find_pid_on_port,  # pyright: ignore[reportPrivateUsage]
    _is_vox_daemon_process,  # pyright: ignore[reportPrivateUsage]
    _kill_pid,  # pyright: ignore[reportPrivateUsage]
    _kill_stale_daemon,  # pyright: ignore[reportPrivateUsage]
    _launchd_install,  # pyright: ignore[reportPrivateUsage]
    _launchd_plist_content,  # pyright: ignore[reportPrivateUsage]
    _launchd_stop,  # pyright: ignore[reportPrivateUsage]
    _safe_systemd_value,  # pyright: ignore[reportPrivateUsage]
    _systemd_audio_env_lines,  # pyright: ignore[reportPrivateUsage]
    _systemd_install,  # pyright: ignore[reportPrivateUsage]
    _systemd_stop,  # pyright: ignore[reportPrivateUsage]
    _systemd_unit_content,  # pyright: ignore[reportPrivateUsage]
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
    current = tmp_path / "current" / "bin"
    current.mkdir(parents=True)
    (current / "voxd").write_text("current")
    (current / "voxd").chmod(0o755)
    (current / "python").write_text("#!/bin/sh\n")
    (current / "python").chmod(0o755)

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


def test_voxd_exec_args_rejects_non_executable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A voxd file without the executable bit must raise SystemExit.

    ``Path.exists()`` returns True for non-executable files, so the
    earlier implementation would happily bake a bad ``ExecStart=`` into
    the systemd unit and fail at runtime with an opaque error. The
    install now probes ``os.access(X_OK)`` and fails fast with a clear
    message. Copilot 3048402463 on PR #162.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_python = bin_dir / "python"
    fake_python.write_text("#!/bin/sh\n")
    fake_python.chmod(0o755)
    # voxd exists but is NOT executable.
    voxd = bin_dir / "voxd"
    voxd.write_text("#!/bin/sh\n")
    voxd.chmod(0o644)

    monkeypatch.setattr("punt_vox.service.sys.executable", str(fake_python))
    with pytest.raises(SystemExit, match="not executable"):
        _voxd_exec_args()


def test_voxd_exec_args_rejects_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A directory at the voxd path must raise SystemExit, not succeed.

    ``Path.exists()`` returns True for directories, so the earlier
    implementation would let a directory named ``voxd`` pass the check
    and produce a broken systemd unit. ``Path.is_file()`` excludes
    directories. Copilot 3048402463 on PR #162.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_python = bin_dir / "python"
    fake_python.write_text("#!/bin/sh\n")
    fake_python.chmod(0o755)
    # voxd is a directory, not a file.
    (bin_dir / "voxd").mkdir()

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
def test_launchd_plist_log_paths_use_current_user_home(
    _mock_exec: MagicMock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Log paths in the plist come from the invoking user's ``$HOME``.

    Install runs as the user now — no sudo escalation — so
    ``Path.home()`` is always the installing user's home. Previously
    service.py had to route through ``_user_state_dir_for(user)`` to
    work around sudo's ``$HOME=/var/root``; that workaround is gone.
    """
    fake_home = tmp_path / "Users" / "deploy"
    fake_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))

    content = _launchd_plist_content("deploy")
    expected_stdout = str(fake_home / ".punt-labs" / "vox" / "logs" / "voxd-stdout.log")
    expected_stderr = str(fake_home / ".punt-labs" / "vox" / "logs" / "voxd-stderr.log")
    assert expected_stdout in content
    assert expected_stderr in content


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
def test_systemd_unit_xdg_fallback_without_env(
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
    mock_run.return_value = MagicMock(returncode=0, stdout="12345\n67890\n")
    assert _find_pid_on_port(8421) == [12345, 67890]


@patch("punt_vox.service.platform.system", return_value="Linux")
@patch("punt_vox.service.subprocess.run")
def test_find_pid_on_port_linux(mock_run: MagicMock, _mock_sys: MagicMock) -> None:
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
    mock_kill.side_effect = [None, ProcessLookupError]
    assert _kill_pid(100) is True
    assert mock_kill.call_args_list[0] == call(100, signal.SIGTERM)
    assert mock_kill.call_args_list[1] == call(100, 0)


@patch("punt_vox.service.os.kill")
def test_kill_pid_already_gone(mock_kill: MagicMock) -> None:
    mock_kill.side_effect = ProcessLookupError
    assert _kill_pid(100) is True
    mock_kill.assert_called_once_with(100, signal.SIGTERM)


@patch("punt_vox.service.os.kill", side_effect=PermissionError)
def test_kill_pid_permission_error(mock_kill: MagicMock) -> None:
    assert _kill_pid(100) is False
    mock_kill.assert_called_once_with(100, signal.SIGTERM)


@patch("punt_vox.service.time.sleep")
@patch("punt_vox.service.time.monotonic")
@patch("punt_vox.service.os.kill")
def test_kill_pid_sigkill_after_timeout(
    mock_kill: MagicMock, mock_monotonic: MagicMock, _mock_sleep: MagicMock
) -> None:
    mock_kill.side_effect = [None, None, None, ProcessLookupError]
    mock_monotonic.side_effect = [0.0, 0.0, 6.0, 6.0, 6.0]
    assert _kill_pid(100) is True
    assert mock_kill.call_args_list == [
        call(100, signal.SIGTERM),
        call(100, 0),
        call(100, signal.SIGKILL),
        call(100, 0),
    ]


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
# _ensure_port_free
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
    keys_path = tmp_path / "keys.env"
    _write_keys_env({"OPENAI_API_KEY": "sk-test"}, keys_path)
    mode = stat_mod.S_IMODE(os.stat(keys_path).st_mode)
    assert mode == 0o600, f"keys.env mode is {oct(mode)}, expected 0o600"


def test_write_keys_env_preserves_existing_keys(tmp_path: Path) -> None:
    """Keys already in the file are preserved when not overridden."""
    keys_path = tmp_path / "keys.env"
    keys_path.write_text(
        "# header\nELEVENLABS_API_KEY=original-eleven\nOPENAI_API_KEY=original-openai\n"
    )
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


def test_write_keys_env_rejects_control_chars_in_value(tmp_path: Path) -> None:
    """Values containing newlines or NUL bytes are dropped, not written.

    Prevents an attacker-controlled env var from smuggling extra
    key=value lines into keys.env. This is input sanitization, not a
    privilege defense — it applies equally when install runs as the
    user.
    """
    keys_path = tmp_path / "keys.env"
    _write_keys_env(
        {
            "OPENAI_API_KEY": "sk-legit",
            "ELEVENLABS_API_KEY": "sk-evil\nAWS_ACCESS_KEY_ID=injected",
        },
        keys_path,
    )
    content = keys_path.read_text()
    assert "OPENAI_API_KEY=sk-legit" in content
    assert "injected" not in content
    assert "ELEVENLABS_API_KEY" not in content


def test_write_keys_env_handles_unreadable_existing_file(tmp_path: Path) -> None:
    """Non-UTF-8 bytes in an existing keys.env do not crash the install.

    Previously ``keys_path.read_text()`` would raise ``UnicodeDecodeError``
    on corrupted bytes and abort the whole install with a stack trace.
    ``_write_keys_env`` now catches the error, logs a warning, and
    overwrites with the clean env. Copilot 3048295101 on PR #162.
    """
    keys_path = tmp_path / "keys.env"
    # Pre-populate with non-UTF-8 bytes — e.g. corrupted file, or a
    # binary blob that an attacker dropped there.
    keys_path.write_bytes(b"\xff\xfe garbage \x00\x01\x02")

    _write_keys_env(
        {"OPENAI_API_KEY": "sk-clean", "TTS_PROVIDER": "openai"},
        keys_path,
    )

    # The call must succeed and produce a clean file containing only
    # the env values passed in.
    content = keys_path.read_text()
    assert "OPENAI_API_KEY=sk-clean" in content
    assert "TTS_PROVIDER=openai" in content
    # The garbage must be gone.
    assert "garbage" not in content
    # Mode is still 0600.
    mode = stat_mod.S_IMODE(os.stat(keys_path).st_mode)
    assert mode == 0o600


def test_write_keys_env_handles_unreadable_existing_file_oserror(
    tmp_path: Path,
) -> None:
    """OSError (e.g. permission denied) during read_text is non-fatal.

    Same policy as the non-UTF-8 case: log a warning and overwrite.
    Exercises the ``OSError`` arm of the ``except (OSError,
    UnicodeDecodeError)`` clause by making the file itself unreadable
    via chmod 000 (which causes ``read_text`` to raise
    ``PermissionError``, a subclass of ``OSError``).
    """
    keys_path = tmp_path / "keys.env"
    keys_path.write_text("OPENAI_API_KEY=stale\n")
    # Make unreadable even to the owner. 000 on a regular file means
    # ``open()`` fails with PermissionError, which is exactly the
    # OSError arm we want to exercise.
    keys_path.chmod(0o000)
    try:
        _write_keys_env({"OPENAI_API_KEY": "sk-new"}, keys_path)
    finally:
        # Ensure the file is readable again before pytest's tmp_path
        # cleanup runs.
        keys_path.chmod(0o600)

    content = keys_path.read_text()
    assert "OPENAI_API_KEY=sk-new" in content
    assert "stale" not in content


def test_write_keys_env_never_exposes_world_readable_state(
    tmp_path: Path,
) -> None:
    """keys.env must never exist at wider-than-0600 permissions.

    The earlier implementation used ``Path.write_text`` + ``Path.chmod``,
    which creates the file via ``open(..., "w")`` — that creates with
    ``0o666 & ~umask`` (typically 0o644 on umask 0022) and only chmods
    afterward. The file was world-readable for the few instructions
    between create and chmod, a real API-key exposure window. The
    fix uses ``os.open(..., O_CREAT, 0o600)`` which sets the mode at
    create time. Copilot 3048402515 on PR #162.

    This test sets a permissive umask (0002) that would have produced
    0o664 under the old code, then asserts the resulting file is
    exactly 0o600. The umask is restored on teardown.
    """
    old_umask = os.umask(0o002)
    try:
        keys_path = tmp_path / "keys.env"
        _write_keys_env({"OPENAI_API_KEY": "sk-test"}, keys_path)
        mode = stat_mod.S_IMODE(os.stat(keys_path).st_mode)
        assert mode == 0o600, (
            f"keys.env mode is {oct(mode)} under umask 0002 — the "
            "os.open path should create the file at exactly 0o600 "
            "regardless of umask, not rely on a post-write chmod."
        )
    finally:
        os.umask(old_umask)


def test_write_keys_env_tightens_parent_dir(tmp_path: Path) -> None:
    """``_write_keys_env`` enforces mode 0700 on the parent dir.

    The state dir may have been created by an earlier version of vox
    (or by hand) with umask-widened permissions (0755 on a typical
    0022 umask). Without this step, a secrets file inside a
    world-traversable directory would let other local users read the
    dir listing and mount further attacks. Fixed by chmod'ing the
    parent to 0700 on every call, even when the dir pre-exists.
    Copilot 3048402424 on PR #162.
    """
    state_root = tmp_path / ".punt-labs" / "vox"
    state_root.mkdir(parents=True)
    # Simulate an older install that left the dir at 0755.
    state_root.chmod(0o755)
    assert stat_mod.S_IMODE(os.stat(state_root).st_mode) == 0o755

    keys_path = state_root / "keys.env"
    _write_keys_env({"OPENAI_API_KEY": "sk-test"}, keys_path)

    mode = stat_mod.S_IMODE(os.stat(state_root).st_mode)
    assert mode == 0o700, (
        f"parent dir mode is {oct(mode)} after _write_keys_env; "
        "the helper must tighten the parent to 0700 so a secrets "
        "file does not live under a world-traversable dir."
    )


# ---------------------------------------------------------------------------
# _ensure_user_dirs — end-to-end with tmp HOME
# ---------------------------------------------------------------------------


def test_ensure_user_dirs_creates_tree_under_current_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_ensure_user_dirs creates the tree and returns the state root."""
    fake_home = tmp_path / "home" / "testuser"
    fake_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))

    result = _ensure_user_dirs()
    expected = fake_home / ".punt-labs" / "vox"
    assert result == expected
    assert expected.is_dir()
    assert (expected / "logs").is_dir()
    assert (expected / "run").is_dir()
    assert (expected / "cache").is_dir()


# ---------------------------------------------------------------------------
# install() — end-to-end under a tmp HOME
# ---------------------------------------------------------------------------


@patch("punt_vox.service._systemd_status", return_value=True)
@patch("punt_vox.service._launchd_status", return_value=True)
@patch("punt_vox.service._systemd_install")
@patch("punt_vox.service._launchd_install")
def test_install_runs_as_user_creates_keys_env(
    _mock_launchd: MagicMock,
    _mock_systemd: MagicMock,
    _mock_launchd_status: MagicMock,
    _mock_systemd_status: MagicMock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """install() writes keys.env under the current user's home, mode 0600.

    Runs with the real ``getpass.getuser`` and ``Path.home`` against a
    tmp HOME — no sudo, no SUDO_USER, no cross-user resolution. The
    resulting keys.env must exist at ``~/.punt-labs/vox/keys.env`` and
    be chmod 0600.
    """
    fake_home = tmp_path / "home" / "user"
    fake_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))

    # Force the voxd exec resolution to a fake binary so install()
    # doesn't depend on where the test runs.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "voxd").write_text("#!/bin/sh\n")
    (bin_dir / "voxd").chmod(0o755)
    (bin_dir / "python").write_text("#!/bin/sh\n")
    (bin_dir / "python").chmod(0o755)
    monkeypatch.setattr("punt_vox.service.sys.executable", str(bin_dir / "python"))

    # Give install() provider keys to snapshot.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-openai")
    monkeypatch.setenv("TTS_PROVIDER", "openai")
    # Stub the port pre-flight and the service-manager stops so the
    # test does not poke the host's real port 8421 or shell out to
    # sudo launchctl/systemctl.
    monkeypatch.setattr("punt_vox.service._ensure_port_free", lambda: None)
    monkeypatch.setattr("punt_vox.service._launchd_stop", lambda: None)
    monkeypatch.setattr("punt_vox.service._systemd_stop", lambda: None)

    # Stub geteuid so the install() root-refusal check passes on CI
    # images that run pytest as uid 0 (e.g. Docker).
    def _euid_nonroot() -> int:
        return 1000

    monkeypatch.setattr("punt_vox.service.os.geteuid", _euid_nonroot)

    install()

    keys_path = fake_home / ".punt-labs" / "vox" / "keys.env"
    assert keys_path.exists(), f"install() failed to create {keys_path}"
    mode = stat_mod.S_IMODE(os.stat(keys_path).st_mode)
    assert mode == 0o600, f"keys.env mode is {oct(mode)}, expected 0o600"
    content = keys_path.read_text()
    assert "OPENAI_API_KEY=sk-test-openai" in content
    assert "TTS_PROVIDER=openai" in content


@patch("punt_vox.service._systemd_status", return_value=True)
@patch("punt_vox.service.subprocess.run")
def test_systemd_install_invokes_expected_sudo_commands(
    mock_run: MagicMock,
    _mock_status: MagicMock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_systemd_install issues four sudo subprocess calls in order.

    Order: ``sudo install`` (places unit), ``sudo systemctl
    daemon-reload``, ``sudo systemctl enable voxd`` (boot
    persistence), ``sudo systemctl restart voxd`` (unconditional
    cycle so upgrades pick up new ExecStart). The ``restart`` step is
    the regression guard for Cursor Bugbot 3048294138 / Copilot
    3048295072.
    """
    fake_home = tmp_path / "home" / "jfreeman"
    fake_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "voxd").write_text("#!/bin/sh\n")
    (bin_dir / "voxd").chmod(0o755)
    (bin_dir / "python").write_text("#!/bin/sh\n")
    (bin_dir / "python").chmod(0o755)
    monkeypatch.setattr("punt_vox.service.sys.executable", str(bin_dir / "python"))

    # Pre-create state dir so tmp unit write succeeds without needing
    # install()'s _ensure_user_dirs step.
    (fake_home / ".punt-labs" / "vox").mkdir(parents=True)

    mock_run.return_value = MagicMock(returncode=0)
    monkeypatch.setattr("punt_vox.service._ensure_port_free", lambda: None)

    _systemd_install("jfreeman")

    sudo_calls = [c for c in mock_run.call_args_list if c[0][0][0] == "sudo"]
    assert len(sudo_calls) == 4, (
        f"Expected 4 sudo calls, got {len(sudo_calls)}: {[c[0][0] for c in sudo_calls]}"
    )
    # Call 1: install the unit file
    assert sudo_calls[0][0][0][:2] == ["sudo", "install"]
    assert "/etc/systemd/system/voxd.service" in sudo_calls[0][0][0]
    # Call 2: daemon-reload
    assert sudo_calls[1][0][0] == ["sudo", "systemctl", "daemon-reload"]
    # Call 3: enable voxd (no --now — boot persistence only)
    assert sudo_calls[2][0][0] == ["sudo", "systemctl", "enable", "voxd"]
    # Call 4: restart voxd (unconditional cycle to pick up new unit)
    assert sudo_calls[3][0][0] == ["sudo", "systemctl", "restart", "voxd"]


@patch("punt_vox.service._systemd_status", return_value=True)
@patch("punt_vox.service.subprocess.run")
def test_systemd_install_restarts_already_running_voxd(
    mock_run: MagicMock,
    _mock_status: MagicMock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression guard: ``restart`` is called unconditionally.

    ``systemctl enable --now`` only starts stopped services; on
    upgrade from an older install it would leave the previous voxd
    running with the stale ``ExecStart``. The install path must
    always call ``systemctl restart`` so the running process picks
    up the new unit file. Cursor Bugbot 3048294138 / Copilot
    3048295072 on PR #162.
    """
    fake_home = tmp_path / "home" / "jfreeman"
    fake_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "voxd").write_text("#!/bin/sh\n")
    (bin_dir / "voxd").chmod(0o755)
    (bin_dir / "python").write_text("#!/bin/sh\n")
    (bin_dir / "python").chmod(0o755)
    monkeypatch.setattr("punt_vox.service.sys.executable", str(bin_dir / "python"))
    (fake_home / ".punt-labs" / "vox").mkdir(parents=True)

    mock_run.return_value = MagicMock(returncode=0)
    monkeypatch.setattr("punt_vox.service._ensure_port_free", lambda: None)

    _systemd_install("jfreeman")

    restart_calls = [
        c
        for c in mock_run.call_args_list
        if c[0][0][:4] == ["sudo", "systemctl", "restart", "voxd"]
    ]
    assert len(restart_calls) == 1, (
        "Expected exactly one `sudo systemctl restart voxd` call; the "
        "install path must cycle the running daemon unconditionally so "
        "upgrades pick up the new ExecStart. "
        f"Actual sudo calls: {[c[0][0] for c in mock_run.call_args_list if c[0][0][0] == 'sudo']}"  # noqa: E501
    )


@patch("punt_vox.service._launchd_status", return_value=True)
@patch("punt_vox.service.subprocess.run")
def test_launchd_install_invokes_expected_sudo_commands(
    mock_run: MagicMock,
    _mock_status: MagicMock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_launchd_install`` issues three sudo subprocess calls in order.

    Order: ``sudo install`` (places plist), ``sudo launchctl load``,
    ``sudo launchctl kickstart -k system/<label>`` (force restart so
    the running daemon picks up the new ``ExecStart``). The upfront
    ``launchctl unload`` has been hoisted out to ``_launchd_stop``,
    which ``install()`` calls BEFORE ``_ensure_port_free`` — so it is
    not part of this standalone test. The ``kickstart -k`` step is
    the regression guard for Cursor Bugbot 3048294138 / Copilot
    3048295072.
    """
    fake_home = tmp_path / "home" / "jfreeman"
    fake_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "voxd").write_text("#!/bin/sh\n")
    (bin_dir / "voxd").chmod(0o755)
    (bin_dir / "python").write_text("#!/bin/sh\n")
    (bin_dir / "python").chmod(0o755)
    monkeypatch.setattr("punt_vox.service.sys.executable", str(bin_dir / "python"))

    (fake_home / ".punt-labs" / "vox").mkdir(parents=True)

    mock_run.return_value = MagicMock(returncode=0)
    monkeypatch.setattr("punt_vox.service._ensure_port_free", lambda: None)

    _launchd_install("jfreeman")

    sudo_calls = [c for c in mock_run.call_args_list if c[0][0][0] == "sudo"]
    assert len(sudo_calls) == 3, (
        f"Expected 3 sudo calls, got {len(sudo_calls)}: {[c[0][0] for c in sudo_calls]}"
    )
    # Call 1: install plist into /Library/LaunchDaemons
    assert sudo_calls[0][0][0][:2] == ["sudo", "install"]
    assert "/Library/LaunchDaemons/com.punt-labs.voxd.plist" in sudo_calls[0][0][0]
    # Call 2: load
    assert sudo_calls[1][0][0][:3] == ["sudo", "launchctl", "load"]
    # Call 3: kickstart -k to force restart
    assert sudo_calls[2][0][0] == [
        "sudo",
        "launchctl",
        "kickstart",
        "-k",
        "system/com.punt-labs.voxd",
    ]


@patch("punt_vox.service._launchd_status", return_value=True)
@patch("punt_vox.service.subprocess.run")
def test_launchd_install_restarts_already_running_voxd(
    mock_run: MagicMock,
    _mock_status: MagicMock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression guard: ``kickstart -k`` is called unconditionally.

    ``launchctl load`` on an already-loaded plist is a no-op and does
    not restart the daemon; the running voxd would keep its stale
    ``ExecStart``. The install path must always call ``launchctl
    kickstart -k`` so the running process is forcibly cycled. Cursor
    Bugbot 3048294138 / Copilot 3048295072 on PR #162.
    """
    fake_home = tmp_path / "home" / "jfreeman"
    fake_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "voxd").write_text("#!/bin/sh\n")
    (bin_dir / "voxd").chmod(0o755)
    (bin_dir / "python").write_text("#!/bin/sh\n")
    (bin_dir / "python").chmod(0o755)
    monkeypatch.setattr("punt_vox.service.sys.executable", str(bin_dir / "python"))
    (fake_home / ".punt-labs" / "vox").mkdir(parents=True)

    mock_run.return_value = MagicMock(returncode=0)
    monkeypatch.setattr("punt_vox.service._ensure_port_free", lambda: None)

    _launchd_install("jfreeman")

    kickstart_calls = [
        c
        for c in mock_run.call_args_list
        if c[0][0][:4] == ["sudo", "launchctl", "kickstart", "-k"]
    ]
    assert len(kickstart_calls) == 1, (
        "Expected exactly one `sudo launchctl kickstart -k` call; the "
        "install path must force-restart the running daemon so upgrades "
        "pick up the new ExecStart. "
        f"Actual sudo calls: {[c[0][0] for c in mock_run.call_args_list if c[0][0][0] == 'sudo']}"  # noqa: E501
    )


@patch("punt_vox.service._systemd_status", return_value=True)
@patch("punt_vox.service.subprocess.run")
def test_systemd_install_writes_unit_to_user_tmp_first(
    mock_run: MagicMock,
    _mock_status: MagicMock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The systemd unit is materialized as a user-owned tmp file first.

    The tmp file must exist at ``~/.punt-labs/vox/voxd.service.tmp``
    when the first ``sudo install`` fires so ``install(1)`` has
    something to copy. After all sudo calls complete, the tmp file
    is removed.
    """
    fake_home = tmp_path / "home" / "jfreeman"
    fake_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))
    (fake_home / ".punt-labs" / "vox").mkdir(parents=True)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "voxd").write_text("#!/bin/sh\n")
    (bin_dir / "voxd").chmod(0o755)
    (bin_dir / "python").write_text("#!/bin/sh\n")
    (bin_dir / "python").chmod(0o755)
    monkeypatch.setattr("punt_vox.service.sys.executable", str(bin_dir / "python"))
    monkeypatch.setattr("punt_vox.service._ensure_port_free", lambda: None)

    tmp_unit_path = fake_home / ".punt-labs" / "vox" / "voxd.service.tmp"
    observed_during_sudo: list[bool] = []

    def _capture_install(*args: object, **kwargs: object) -> MagicMock:
        del args, kwargs
        observed_during_sudo.append(tmp_unit_path.exists())
        return MagicMock(returncode=0)

    mock_run.side_effect = _capture_install

    _systemd_install("jfreeman")

    # The first sudo call saw the tmp file on disk.
    assert observed_during_sudo, "no subprocess.run calls were observed"
    assert observed_during_sudo[0] is True, (
        f"tmp unit file {tmp_unit_path} did not exist when first sudo ran"
    )
    # After the install completes, the tmp file is cleaned up.
    assert not tmp_unit_path.exists(), (
        f"tmp unit file {tmp_unit_path} was not removed after install"
    )


def test_install_does_not_chown_anything(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """install() must never invoke os.chown / os.lchown / os.fchown.

    Regression guard: the old sudo-based install ran as root inside a
    user-controlled directory and had to chown every created path
    back to the installing user. That pattern is gone — install now
    runs as the user from start to finish — so any chown call is a
    bug indicating a regression back to root-execution.
    """
    fake_home = tmp_path / "home" / "user"
    fake_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "voxd").write_text("#!/bin/sh\n")
    (bin_dir / "voxd").chmod(0o755)
    (bin_dir / "python").write_text("#!/bin/sh\n")
    (bin_dir / "python").chmod(0o755)
    monkeypatch.setattr("punt_vox.service.sys.executable", str(bin_dir / "python"))

    chown_calls: list[tuple[str, object, int, int]] = []

    def _record_chown(path: object, uid: int, gid: int) -> None:
        chown_calls.append(("chown", path, uid, gid))

    def _record_lchown(path: object, uid: int, gid: int) -> None:
        chown_calls.append(("lchown", path, uid, gid))

    def _record_fchown(fd: int, uid: int, gid: int) -> None:
        chown_calls.append(("fchown", fd, uid, gid))

    monkeypatch.setattr("punt_vox.service.os.chown", _record_chown, raising=False)
    # os.lchown / os.fchown may not be imported into the service module
    # (they were deleted). Patch them on the real os module instead so
    # any stray call from any code path still gets recorded.
    monkeypatch.setattr("os.chown", _record_chown)
    monkeypatch.setattr("os.lchown", _record_lchown)
    monkeypatch.setattr("os.fchown", _record_fchown)

    # Stub out the platform-specific install path so we exercise the
    # user-owned code in install() without touching real system dirs.
    def _noop_install(_user: str) -> None:
        return None

    def _always_running() -> bool:
        return True

    monkeypatch.setattr("punt_vox.service._launchd_install", _noop_install)
    monkeypatch.setattr("punt_vox.service._systemd_install", _noop_install)
    monkeypatch.setattr("punt_vox.service._launchd_status", _always_running)
    monkeypatch.setattr("punt_vox.service._systemd_status", _always_running)
    # Stub the port pre-flight and the service-manager stops so the
    # test does not poke at the host's real port 8421 (which might be
    # holding a developer's running daemon — this test must be
    # hermetic) or shell out to sudo launchctl/systemctl.
    monkeypatch.setattr("punt_vox.service._ensure_port_free", lambda: None)
    monkeypatch.setattr("punt_vox.service._launchd_stop", lambda: None)
    monkeypatch.setattr("punt_vox.service._systemd_stop", lambda: None)

    # Force non-root uid so the install root-refusal check passes on
    # CI images that run pytest as uid 0.
    def _euid_nonroot() -> int:
        return 1000

    monkeypatch.setattr("punt_vox.service.os.geteuid", _euid_nonroot)

    install()

    assert chown_calls == [], (
        "install() must not call os.chown/os.lchown/os.fchown — "
        f"observed: {chown_calls}"
    )


@patch("punt_vox.service._systemd_status", return_value=False)
@patch("punt_vox.service._systemd_install")
@patch("punt_vox.service._launchd_status", return_value=False)
@patch("punt_vox.service._launchd_install")
@patch("punt_vox.service.detect_platform", return_value="macos")
def test_install_reports_not_running(
    _mock_platform: MagicMock,
    _mock_launchd: MagicMock,
    _mock_launchd_status: MagicMock,
    _mock_systemd: MagicMock,
    _mock_systemd_status: MagicMock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """install() reports 'not yet running' when the service is down."""
    fake_home = tmp_path / "home" / "user"
    fake_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "voxd").write_text("#!/bin/sh\n")
    (bin_dir / "voxd").chmod(0o755)
    (bin_dir / "python").write_text("#!/bin/sh\n")
    (bin_dir / "python").chmod(0o755)
    monkeypatch.setattr("punt_vox.service.sys.executable", str(bin_dir / "python"))
    monkeypatch.setattr("punt_vox.service._ensure_port_free", lambda: None)
    monkeypatch.setattr("punt_vox.service._launchd_stop", lambda: None)
    monkeypatch.setattr("punt_vox.service._systemd_stop", lambda: None)

    def _euid_nonroot() -> int:
        return 1000

    monkeypatch.setattr("punt_vox.service.os.geteuid", _euid_nonroot)

    result = install()
    assert "not yet running" in result


# ---------------------------------------------------------------------------
# install() — pre-flight stop before port check (Cursor Bugbot 3048416720)
# ---------------------------------------------------------------------------


@patch("punt_vox.service._systemd_status", return_value=True)
@patch("punt_vox.service._systemd_install")
@patch("punt_vox.service.detect_platform", return_value="linux")
def test_install_runs_systemd_stop_before_port_check(
    _mock_platform: MagicMock,
    _mock_systemd_install: MagicMock,
    _mock_status: MagicMock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``install()`` must stop voxd via systemctl BEFORE the port check.

    Without the pre-flight stop, systemd's ``Restart=on-failure`` would
    respawn the daemon the instant ``_ensure_port_free`` kills the
    stale process, leaving the upgrade racy and the old binary alive.
    This test records the order of events (``_systemd_stop`` call and
    ``_ensure_port_free`` call) and asserts the stop happens first.
    Cursor Bugbot 3048416720 on PR #162.
    """
    fake_home = tmp_path / "home" / "user"
    fake_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "voxd").write_text("#!/bin/sh\n")
    (bin_dir / "voxd").chmod(0o755)
    (bin_dir / "python").write_text("#!/bin/sh\n")
    (bin_dir / "python").chmod(0o755)
    monkeypatch.setattr("punt_vox.service.sys.executable", str(bin_dir / "python"))

    def _euid_nonroot() -> int:
        return 1000

    monkeypatch.setattr("punt_vox.service.os.geteuid", _euid_nonroot)

    call_order: list[str] = []

    def _record_stop() -> None:
        call_order.append("systemd_stop")

    def _record_port_free() -> None:
        call_order.append("ensure_port_free")

    monkeypatch.setattr("punt_vox.service._systemd_stop", _record_stop)
    monkeypatch.setattr("punt_vox.service._ensure_port_free", _record_port_free)

    install()

    assert "systemd_stop" in call_order, "install() did not call _systemd_stop"
    assert "ensure_port_free" in call_order, "install() did not call _ensure_port_free"
    assert call_order.index("systemd_stop") < call_order.index("ensure_port_free"), (
        f"_systemd_stop must run before _ensure_port_free, got order: {call_order}"
    )


@patch("punt_vox.service._launchd_status", return_value=True)
@patch("punt_vox.service._launchd_install")
@patch("punt_vox.service.detect_platform", return_value="macos")
def test_install_runs_launchd_stop_before_port_check(
    _mock_platform: MagicMock,
    _mock_launchd_install: MagicMock,
    _mock_status: MagicMock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``install()`` must unload voxd from launchd BEFORE the port check.

    Without the pre-flight unload, launchd's ``KeepAlive=true`` would
    respawn the daemon the instant ``_ensure_port_free`` kills the
    stale process, leaving the upgrade racy and the old binary alive.
    This test records the order of events (``_launchd_stop`` call and
    ``_ensure_port_free`` call) and asserts the stop happens first.
    Cursor Bugbot 3048416720 on PR #162.
    """
    fake_home = tmp_path / "home" / "user"
    fake_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "voxd").write_text("#!/bin/sh\n")
    (bin_dir / "voxd").chmod(0o755)
    (bin_dir / "python").write_text("#!/bin/sh\n")
    (bin_dir / "python").chmod(0o755)
    monkeypatch.setattr("punt_vox.service.sys.executable", str(bin_dir / "python"))

    def _euid_nonroot() -> int:
        return 1000

    monkeypatch.setattr("punt_vox.service.os.geteuid", _euid_nonroot)

    call_order: list[str] = []

    def _record_stop() -> None:
        call_order.append("launchd_stop")

    def _record_port_free() -> None:
        call_order.append("ensure_port_free")

    monkeypatch.setattr("punt_vox.service._launchd_stop", _record_stop)
    monkeypatch.setattr("punt_vox.service._ensure_port_free", _record_port_free)

    install()

    assert "launchd_stop" in call_order, "install() did not call _launchd_stop"
    assert "ensure_port_free" in call_order, "install() did not call _ensure_port_free"
    assert call_order.index("launchd_stop") < call_order.index("ensure_port_free"), (
        f"_launchd_stop must run before _ensure_port_free, got order: {call_order}"
    )


# ---------------------------------------------------------------------------
# _launchd_stop / _systemd_stop — pre-flight helpers (idempotent)
# ---------------------------------------------------------------------------


@patch("punt_vox.service.subprocess.run")
@patch("punt_vox.service._LAUNCHD_PLIST")
def test_launchd_stop_noop_when_plist_missing(
    mock_plist: MagicMock,
    mock_run: MagicMock,
) -> None:
    """Fresh install (no prior plist): _launchd_stop skips the sudo call."""
    mock_plist.exists.return_value = False
    _launchd_stop()
    mock_run.assert_not_called()


@patch("punt_vox.service.subprocess.run")
@patch("punt_vox.service._LAUNCHD_PLIST")
def test_launchd_stop_unloads_when_plist_present(
    mock_plist: MagicMock,
    mock_run: MagicMock,
) -> None:
    """Existing plist: _launchd_stop issues sudo launchctl unload -w."""
    mock_plist.exists.return_value = True
    mock_run.return_value = MagicMock(returncode=0)

    _launchd_stop()

    mock_run.assert_called_once()
    call_args = mock_run.call_args
    # The first four args are the fixed prefix — the fifth is the plist
    # path which comes from the mocked _LAUNCHD_PLIST, so we only
    # verify the prefix and the kwargs.
    assert call_args[0][0][:4] == ["sudo", "launchctl", "unload", "-w"]
    # check=False — unload is allowed to fail when not actually loaded
    assert call_args[1]["check"] is False


@patch("punt_vox.service.subprocess.run")
@patch("punt_vox.service._SYSTEMD_UNIT")
def test_systemd_stop_noop_when_unit_missing(
    mock_unit: MagicMock,
    mock_run: MagicMock,
) -> None:
    """Fresh install (no prior unit): _systemd_stop skips the sudo call."""
    mock_unit.exists.return_value = False
    _systemd_stop()
    mock_run.assert_not_called()


@patch("punt_vox.service.subprocess.run")
@patch("punt_vox.service._SYSTEMD_UNIT")
def test_systemd_stop_stops_when_unit_present(
    mock_unit: MagicMock,
    mock_run: MagicMock,
) -> None:
    """Existing unit: _systemd_stop issues sudo systemctl stop voxd."""
    mock_unit.exists.return_value = True
    mock_run.return_value = MagicMock(returncode=0)

    _systemd_stop()

    mock_run.assert_called_once()
    call_args = mock_run.call_args
    assert call_args[0][0] == ["sudo", "systemctl", "stop", "voxd"]
    # check=False — stop is allowed to fail when not actually running
    assert call_args[1]["check"] is False


# ---------------------------------------------------------------------------
# install() — refuses to run as root (Copilot 3048295090 regression guard)
# ---------------------------------------------------------------------------


def test_install_refuses_to_run_as_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """install() raises SystemExit when ``os.geteuid() == 0``.

    Running ``sudo vox daemon install`` would cause ``getpass.getuser``
    to return ``root``, ``Path.home()`` to resolve to ``/root``, and
    all per-user state to land under ``/root/.punt-labs/vox/`` with
    ``User=root`` baked into the generated systemd unit. The install
    command is supposed to run as the normal user and prompt for sudo
    only when it places the unit file. Copilot 3048295090 on PR #162.
    """
    fake_home = tmp_path / "home" / "user"
    fake_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "voxd").write_text("#!/bin/sh\n")
    (bin_dir / "voxd").chmod(0o755)
    (bin_dir / "python").write_text("#!/bin/sh\n")
    (bin_dir / "python").chmod(0o755)
    monkeypatch.setattr("punt_vox.service.sys.executable", str(bin_dir / "python"))

    def _euid_root() -> int:
        return 0

    monkeypatch.setattr("punt_vox.service.os.geteuid", _euid_root)

    with pytest.raises(SystemExit, match="without sudo"):
        install()

    # The refusal must fire before any filesystem work: no state dir,
    # no tmp files, no keys.env.
    assert not (fake_home / ".punt-labs").exists(), (
        "install() created filesystem state before the root-refusal check"
    )
