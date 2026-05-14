"""Tests for punt_vox.service.systemd — Linux systemd backend."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from punt_vox.service import DEFAULT_PORT
from punt_vox.service.process import ProcessManager
from punt_vox.service.systemd import SystemdBackend


@pytest.fixture()
def backend() -> SystemdBackend:
    return SystemdBackend(
        ProcessManager(),
        lambda: ["/usr/local/bin/voxd", "--port", "8421"],
    )


# ---------------------------------------------------------------------------
# systemd unit content
# ---------------------------------------------------------------------------


def test_systemd_unit_contains_exec_start(backend: SystemdBackend) -> None:
    content = backend.unit_content("testuser")
    assert "ExecStart=" in content
    assert "voxd" in content
    assert str(DEFAULT_PORT) in content


def test_systemd_unit_restart_policy(backend: SystemdBackend) -> None:
    content = backend.unit_content("testuser")
    assert "Restart=on-failure" in content
    assert "RestartSec=5" in content


@patch.dict("os.environ", {"PATH": "/usr/local/bin:/usr/bin:/bin"})
def test_systemd_unit_contains_path_from_env(backend: SystemdBackend) -> None:
    content = backend.unit_content("testuser")
    assert 'Environment="PATH=/usr/local/bin:/usr/bin:/bin"' in content


def test_systemd_unit_description(backend: SystemdBackend) -> None:
    content = backend.unit_content("testuser")
    assert "Voxd text-to-speech daemon" in content


def test_systemd_unit_no_runtime_directory(backend: SystemdBackend) -> None:
    """RuntimeDirectory= must NOT be present — state lives in $HOME."""
    content = backend.unit_content("testuser")
    assert "RuntimeDirectory=" not in content
    assert "RuntimeDirectoryMode=" not in content


def test_systemd_unit_no_leading_whitespace(backend: SystemdBackend) -> None:
    """Section headers must start at column 0 — no leading spaces."""
    content = backend.unit_content("testuser")
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
def test_systemd_unit_includes_audio_env_vars(backend: SystemdBackend) -> None:
    """Audio env vars present in the environment appear in the unit file."""
    content = backend.unit_content("testuser")
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
def test_systemd_unit_multiline_env_indented(backend: SystemdBackend) -> None:
    """Multiple Environment= lines each start at column 0 (after dedent)."""
    content = backend.unit_content("testuser")
    env_lines = [ln for ln in content.splitlines() if ln.startswith("Environment=")]
    assert len(env_lines) >= 2
    for line in env_lines:
        assert line == line.lstrip(), (
            f"Environment line has leading whitespace: {line!r}"
        )


@patch.dict("os.environ", {"PATH": "/usr/bin:/bin"}, clear=True)
@patch("punt_vox.service.systemd.pwd.getpwnam")
def test_systemd_unit_xdg_fallback_without_env(
    mock_getpwnam: MagicMock,
    backend: SystemdBackend,
) -> None:
    """When XDG_RUNTIME_DIR is absent, compute from target user UID."""
    mock_getpwnam.return_value = MagicMock(pw_uid=1000)
    content = backend.unit_content("deploy")
    assert 'Environment="XDG_RUNTIME_DIR=/run/user/1000"' in content
    assert "PULSE_SERVER" not in content
    assert "DBUS_SESSION_BUS_ADDRESS" not in content


# ---------------------------------------------------------------------------
# audio env lines
# ---------------------------------------------------------------------------


@patch.dict(
    "os.environ",
    {"XDG_RUNTIME_DIR": "/run/user/1000"},
    clear=True,
)
def test_systemd_audio_env_lines_xdg_from_env(backend: SystemdBackend) -> None:
    """XDG_RUNTIME_DIR from env is used directly — no pwd fallback."""
    lines = backend.audio_env_lines("testuser")
    assert len(lines) == 1
    assert 'Environment="XDG_RUNTIME_DIR=/run/user/1000"' in lines[0]


@patch.dict("os.environ", {}, clear=True)
@patch("punt_vox.service.systemd.pwd.getpwnam")
def test_systemd_audio_env_lines_xdg_fallback(
    mock_getpwnam: MagicMock,
    backend: SystemdBackend,
) -> None:
    """No XDG_RUNTIME_DIR in env triggers pwd-based fallback."""
    mock_getpwnam.return_value = MagicMock(pw_uid=1000)
    lines = backend.audio_env_lines("deploy")
    assert len(lines) == 1
    assert lines[0] == 'Environment="XDG_RUNTIME_DIR=/run/user/1000"'
    mock_getpwnam.assert_called_once_with("deploy")


@patch.dict("os.environ", {}, clear=True)
@patch(
    "punt_vox.service.systemd.pwd.getpwnam",
    side_effect=KeyError("no such user"),
)
def test_systemd_audio_env_lines_fallback_unknown_user(
    _mock_getpwnam: MagicMock,
    backend: SystemdBackend,
) -> None:
    """Unknown user produces empty list — no crash."""
    assert backend.audio_env_lines("ghost") == []


@patch.dict(
    "os.environ",
    {"XDG_RUNTIME_DIR": '/run/user/1000\nExecStartPre=/bin/evil"'},
    clear=True,
)
def test_systemd_audio_env_lines_rejects_unsafe_value(
    backend: SystemdBackend,
) -> None:
    """Values with newlines or quotes are rejected."""
    lines = backend.audio_env_lines("testuser")
    assert not any("evil" in line for line in lines)


# ---------------------------------------------------------------------------
# safe_systemd_value
# ---------------------------------------------------------------------------


def test_safe_systemd_value_accepts_normal() -> None:
    assert SystemdBackend.safe_systemd_value("/run/user/1000") is True
    assert SystemdBackend.safe_systemd_value("unix:path=/run/user/1000/bus") is True


def test_safe_systemd_value_rejects_newline() -> None:
    assert SystemdBackend.safe_systemd_value("foo\nbar") is False


def test_safe_systemd_value_rejects_quote() -> None:
    assert SystemdBackend.safe_systemd_value('foo"bar') is False


def test_safe_systemd_value_rejects_carriage_return() -> None:
    assert SystemdBackend.safe_systemd_value("foo\rbar") is False


def test_safe_systemd_value_rejects_backslash() -> None:
    assert SystemdBackend.safe_systemd_value("foo\\bar") is False


# ---------------------------------------------------------------------------
# _systemd_stop
# ---------------------------------------------------------------------------


@patch("punt_vox.service.systemd.subprocess.run")
@patch("punt_vox.service.systemd._SYSTEMD_UNIT")
def test_systemd_stop_noop_when_unit_missing(
    mock_unit: MagicMock,
    mock_run: MagicMock,
) -> None:
    """Fresh install (no prior unit): stop skips the sudo call."""
    mock_unit.exists.return_value = False
    be = SystemdBackend(ProcessManager(), list)
    be.stop()
    mock_run.assert_not_called()


@patch("punt_vox.service.systemd.subprocess.run")
@patch("punt_vox.service.systemd._SYSTEMD_UNIT")
def test_systemd_stop_stops_when_unit_present(
    mock_unit: MagicMock,
    mock_run: MagicMock,
) -> None:
    """Existing unit: stop issues sudo systemctl stop voxd."""
    mock_unit.exists.return_value = True
    mock_run.return_value = MagicMock(returncode=0)

    be = SystemdBackend(ProcessManager(), list)
    be.stop()

    mock_run.assert_called_once()
    call_args = mock_run.call_args
    assert call_args[0][0] == ["sudo", "systemctl", "stop", "voxd"]
    assert call_args[1]["check"] is False


# ---------------------------------------------------------------------------
# _systemd_install
# ---------------------------------------------------------------------------


@patch("punt_vox.service.systemd.subprocess.run")
def test_systemd_install_invokes_expected_sudo_commands(
    mock_run: MagicMock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """install issues four sudo subprocess calls in order."""
    fake_home = tmp_path / "home" / "jfreeman"
    fake_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))
    (fake_home / ".punt-labs" / "vox").mkdir(parents=True)

    mock_run.return_value = MagicMock(returncode=0)

    be = SystemdBackend(
        ProcessManager(),
        lambda: ["/usr/local/bin/voxd", "--port", "8421"],
    )
    be.install("jfreeman")

    sudo_calls = [c for c in mock_run.call_args_list if c[0][0][0] == "sudo"]
    assert len(sudo_calls) == 4, (
        f"Expected 4 sudo calls, got {len(sudo_calls)}: {[c[0][0] for c in sudo_calls]}"
    )
    assert sudo_calls[0][0][0][:2] == ["sudo", "install"]
    assert "/etc/systemd/system/voxd.service" in sudo_calls[0][0][0]
    assert sudo_calls[1][0][0] == ["sudo", "systemctl", "daemon-reload"]
    assert sudo_calls[2][0][0] == ["sudo", "systemctl", "enable", "voxd"]
    assert sudo_calls[3][0][0] == ["sudo", "systemctl", "restart", "voxd"]


@patch("punt_vox.service.systemd.subprocess.run")
def test_systemd_install_restarts_already_running_voxd(
    mock_run: MagicMock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression guard: ``restart`` is called unconditionally."""
    fake_home = tmp_path / "home" / "jfreeman"
    fake_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))
    (fake_home / ".punt-labs" / "vox").mkdir(parents=True)

    mock_run.return_value = MagicMock(returncode=0)

    be = SystemdBackend(
        ProcessManager(),
        lambda: ["/usr/local/bin/voxd", "--port", "8421"],
    )
    be.install("jfreeman")

    restart_calls = [
        c
        for c in mock_run.call_args_list
        if c[0][0][:4] == ["sudo", "systemctl", "restart", "voxd"]
    ]
    assert len(restart_calls) == 1


@patch("punt_vox.service.systemd.subprocess.run")
def test_systemd_install_writes_unit_to_user_tmp_first(
    mock_run: MagicMock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The systemd unit is materialized as a user-owned tmp file first."""
    fake_home = tmp_path / "home" / "jfreeman"
    fake_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))
    (fake_home / ".punt-labs" / "vox").mkdir(parents=True)

    tmp_unit_path = fake_home / ".punt-labs" / "vox" / "voxd.service.tmp"
    observed_during_sudo: list[bool] = []

    def _capture_install(*args: object, **kwargs: object) -> MagicMock:
        del args, kwargs
        observed_during_sudo.append(tmp_unit_path.exists())
        return MagicMock(returncode=0)

    mock_run.side_effect = _capture_install

    be = SystemdBackend(
        ProcessManager(),
        lambda: ["/usr/local/bin/voxd", "--port", "8421"],
    )
    be.install("jfreeman")

    assert observed_during_sudo, "no subprocess.run calls were observed"
    assert observed_during_sudo[0] is True
    assert not tmp_unit_path.exists()


# ---------------------------------------------------------------------------
# Legacy user-level vox.service cleanup (vox-45r)
# ---------------------------------------------------------------------------


def _stage_legacy_user_unit(fake_home: Path) -> Path:
    """Create a fake ``~/.config/systemd/user/vox.service`` under *fake_home*."""
    unit_dir = fake_home / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    unit = unit_dir / "vox.service"
    unit.write_text(
        "[Unit]\nDescription=stale vox.service from legacy install layout\n"
        "\n[Service]\nExecStart=/home/tester/.local/bin/vox serve --port 8421\n"
        "Restart=on-failure\nRestartSec=5\n"
        "\n[Install]\nWantedBy=default.target\n",
        encoding="utf-8",
    )
    return unit


def test_legacy_user_unit_path_resolves_under_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``legacy_user_unit_path`` is computed from ``Path.home()`` at call time."""
    fake_home = tmp_path / "home" / "user"
    fake_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))
    resolved = SystemdBackend.legacy_user_unit_path()
    assert resolved == fake_home / ".config" / "systemd" / "user" / "vox.service"


def test_cleanup_stale_user_unit_noop_on_non_linux(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """macOS: skip entirely."""
    fake_home = tmp_path / "home" / "user"
    fake_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))
    _stage_legacy_user_unit(fake_home)

    monkeypatch.setattr("punt_vox.service.systemd.platform.system", lambda: "Darwin")
    with patch("punt_vox.service.systemd.subprocess.run") as mock_run:
        result = SystemdBackend.cleanup_stale_user_unit()

    assert result is False
    mock_run.assert_not_called()


def test_cleanup_stale_user_unit_noop_when_file_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Linux machine with no legacy unit: cleanup is a no-op."""
    fake_home = tmp_path / "home" / "user"
    fake_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))

    monkeypatch.setattr("punt_vox.service.systemd.platform.system", lambda: "Linux")
    with patch("punt_vox.service.systemd.subprocess.run") as mock_run:
        result = SystemdBackend.cleanup_stale_user_unit()

    assert result is False
    mock_run.assert_not_called()


def test_cleanup_stale_user_unit_removes_file_and_reloads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Linux + file present: disable --now, unlink, daemon-reload."""
    fake_home = tmp_path / "home" / "user"
    fake_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))
    unit = _stage_legacy_user_unit(fake_home)
    assert unit.exists()

    monkeypatch.setattr("punt_vox.service.systemd.platform.system", lambda: "Linux")
    with patch(
        "punt_vox.service.systemd.subprocess.run",
        return_value=MagicMock(returncode=0),
    ) as mock_run:
        result = SystemdBackend.cleanup_stale_user_unit()

    assert result is True
    assert not unit.exists()

    assert mock_run.call_count == 2
    disable_call, reload_call = mock_run.call_args_list

    assert disable_call[0][0] == [
        "systemctl",
        "--user",
        "disable",
        "--now",
        "vox.service",
    ]
    assert disable_call[1]["check"] is False

    assert reload_call[0][0] == ["systemctl", "--user", "daemon-reload"]
    assert reload_call[1]["check"] is False


def test_cleanup_stale_user_unit_tolerates_failing_disable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-zero disable return must not abort cleanup."""
    fake_home = tmp_path / "home" / "user"
    fake_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))
    unit = _stage_legacy_user_unit(fake_home)

    monkeypatch.setattr("punt_vox.service.systemd.platform.system", lambda: "Linux")
    with patch(
        "punt_vox.service.systemd.subprocess.run",
        return_value=MagicMock(returncode=1),
    ) as mock_run:
        result = SystemdBackend.cleanup_stale_user_unit()

    assert result is True
    assert not unit.exists()
    assert mock_run.call_count == 2


def test_cleanup_stale_user_unit_never_touches_system_unit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scope guard: the cleanup must never touch the system voxd.service."""
    fake_home = tmp_path / "home" / "user"
    fake_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))
    _stage_legacy_user_unit(fake_home)

    monkeypatch.setattr("punt_vox.service.systemd.platform.system", lambda: "Linux")
    with patch(
        "punt_vox.service.systemd.subprocess.run",
        return_value=MagicMock(returncode=0),
    ) as mock_run:
        SystemdBackend.cleanup_stale_user_unit()

    for call_obj in mock_run.call_args_list:
        argv = call_obj[0][0]
        joined = " ".join(argv)
        assert "voxd" not in joined
        assert "sudo" not in argv
        assert "/etc/systemd" not in joined
