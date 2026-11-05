"""Tests for punt_vox.service.systemd_unit -- the voxd unit file."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from punt_vox.service import DEFAULT_PORT
from punt_vox.service.systemd_unit import SystemdUnit


@pytest.fixture()
def unit(tmp_path: Path) -> SystemdUnit:
    return SystemdUnit(
        tmp_path / "voxd.service",
        lambda: ["/usr/local/bin/voxd", "--port", "8421"],
    )


# ---------------------------------------------------------------------------
# systemd unit content
# ---------------------------------------------------------------------------


def test_systemd_unit_contains_exec_start(unit: SystemdUnit) -> None:
    content = unit.content("testuser")
    assert "ExecStart=" in content
    assert "voxd" in content
    assert str(DEFAULT_PORT) in content


def test_systemd_unit_restart_policy(unit: SystemdUnit) -> None:
    content = unit.content("testuser")
    assert "Restart=on-failure" in content
    assert "RestartSec=5" in content


@patch.dict("os.environ", {"PATH": "/usr/local/bin:/usr/bin:/bin"})
def test_systemd_unit_contains_path_from_env(unit: SystemdUnit) -> None:
    content = unit.content("testuser")
    assert 'Environment="PATH=/usr/local/bin:/usr/bin:/bin"' in content


def test_systemd_unit_description(unit: SystemdUnit) -> None:
    content = unit.content("testuser")
    assert "Voxd text-to-speech daemon" in content


def test_systemd_unit_no_runtime_directory(unit: SystemdUnit) -> None:
    """RuntimeDirectory= must NOT be present — state lives in $HOME."""
    content = unit.content("testuser")
    assert "RuntimeDirectory=" not in content
    assert "RuntimeDirectoryMode=" not in content


def test_systemd_unit_no_leading_whitespace(unit: SystemdUnit) -> None:
    """Section headers must start at column 0 — no leading spaces."""
    content = unit.content("testuser")
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
def test_systemd_unit_includes_audio_env_vars(unit: SystemdUnit) -> None:
    """Audio env vars present in the environment appear in the unit file."""
    content = unit.content("testuser")
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
def test_systemd_unit_multiline_env_indented(unit: SystemdUnit) -> None:
    """Multiple Environment= lines each start at column 0 (after dedent)."""
    content = unit.content("testuser")
    env_lines = [ln for ln in content.splitlines() if ln.startswith("Environment=")]
    assert len(env_lines) >= 2
    for line in env_lines:
        assert line == line.lstrip(), (
            f"Environment line has leading whitespace: {line!r}"
        )


@patch.dict("os.environ", {"PATH": "/usr/bin:/bin"}, clear=True)
@patch("punt_vox.service.systemd_unit.pwd.getpwnam")
def test_systemd_unit_xdg_fallback_without_env(
    mock_getpwnam: MagicMock,
    unit: SystemdUnit,
) -> None:
    """When XDG_RUNTIME_DIR is absent, compute from target user UID."""
    mock_getpwnam.return_value = MagicMock(pw_uid=1000)
    content = unit.content("deploy")
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
def test_systemd_audio_env_lines_xdg_from_env(unit: SystemdUnit) -> None:
    """XDG_RUNTIME_DIR from env is used directly — no pwd fallback."""
    lines = unit.audio_env_lines("testuser")
    assert len(lines) == 1
    assert 'Environment="XDG_RUNTIME_DIR=/run/user/1000"' in lines[0]


@patch.dict("os.environ", {}, clear=True)
@patch("punt_vox.service.systemd_unit.pwd.getpwnam")
def test_systemd_audio_env_lines_xdg_fallback(
    mock_getpwnam: MagicMock,
    unit: SystemdUnit,
) -> None:
    """No XDG_RUNTIME_DIR in env triggers pwd-based fallback."""
    mock_getpwnam.return_value = MagicMock(pw_uid=1000)
    lines = unit.audio_env_lines("deploy")
    assert len(lines) == 1
    assert lines[0] == 'Environment="XDG_RUNTIME_DIR=/run/user/1000"'
    mock_getpwnam.assert_called_once_with("deploy")


@patch.dict("os.environ", {}, clear=True)
@patch(
    "punt_vox.service.systemd_unit.pwd.getpwnam",
    side_effect=KeyError("no such user"),
)
def test_systemd_audio_env_lines_fallback_unknown_user(
    _mock_getpwnam: MagicMock,
    unit: SystemdUnit,
) -> None:
    """Unknown user produces empty list — no crash."""
    assert unit.audio_env_lines("ghost") == []


@patch.dict(
    "os.environ",
    {"XDG_RUNTIME_DIR": '/run/user/1000\nExecStartPre=/bin/evil"'},
    clear=True,
)
def test_systemd_audio_env_lines_rejects_unsafe_value(
    unit: SystemdUnit,
) -> None:
    """Values with newlines or quotes are rejected."""
    lines = unit.audio_env_lines("testuser")
    assert not any("evil" in line for line in lines)


# ---------------------------------------------------------------------------
# safe_systemd_value
# ---------------------------------------------------------------------------


def test_safe_systemd_value_accepts_normal() -> None:
    assert SystemdUnit.safe_value("/run/user/1000") is True
    assert SystemdUnit.safe_value("unix:path=/run/user/1000/bus") is True


def test_safe_systemd_value_rejects_newline() -> None:
    assert SystemdUnit.safe_value("foo\nbar") is False


def test_safe_systemd_value_rejects_quote() -> None:
    assert SystemdUnit.safe_value('foo"bar') is False


def test_safe_systemd_value_rejects_carriage_return() -> None:
    assert SystemdUnit.safe_value("foo\rbar") is False


def test_safe_systemd_value_rejects_backslash() -> None:
    assert SystemdUnit.safe_value("foo\\bar") is False


# ---------------------------------------------------------------------------
# installed_bind -- read back from the unit, never from the shell
# ---------------------------------------------------------------------------


@patch.dict("os.environ", {"PATH": "/usr/bin", "VOXD_BIND": "10.0.0.5"}, clear=True)
def test_installed_bind_reads_the_unit_not_the_shell(unit: SystemdUnit) -> None:
    unit.path.write_text(unit.content("someone"))
    with patch.dict("os.environ", {"VOXD_BIND": "10.0.0.9"}):
        assert unit.installed_bind() == "10.0.0.5"


@patch.dict("os.environ", {"PATH": "/usr/bin", "VOXD_BIND": "a%b"}, clear=True)
def test_installed_bind_undoes_percent_escaping(unit: SystemdUnit) -> None:
    unit.path.write_text(unit.content("someone"))
    assert 'Environment="VOXD_BIND=a%%b"' in unit.path.read_text()
    assert unit.installed_bind() == "a%b"


@patch.dict("os.environ", {"PATH": "/usr/bin"}, clear=True)
def test_installed_bind_empty_when_unit_sets_none(unit: SystemdUnit) -> None:
    unit.path.write_text(unit.content("someone"))
    assert unit.installed_bind() == ""


def test_installed_bind_empty_when_not_installed(unit: SystemdUnit) -> None:
    assert unit.installed_bind() == ""
