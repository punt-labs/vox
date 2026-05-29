"""Tests for punt_vox.service.launchd -- macOS launchd backend."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from punt_vox.service import DEFAULT_PORT
from punt_vox.service.launchd import _OLD_LAUNCHD_PLIST, LaunchdBackend
from punt_vox.service.process import ProcessManager


@pytest.fixture()
def backend() -> LaunchdBackend:
    """Create a LaunchdBackend with a stub exec-args function."""
    return LaunchdBackend(
        ProcessManager(),
        lambda: ["/usr/local/bin/voxd", "--port", "8421"],
    )


# ---------------------------------------------------------------------------
# launchd plist content
# ---------------------------------------------------------------------------


def test_launchd_plist_contains_label(backend: LaunchdBackend) -> None:
    content = backend.plist_content()
    assert "com.punt-labs.voxd" in content


def test_launchd_plist_contains_args(backend: LaunchdBackend) -> None:
    content = backend.plist_content()
    assert "voxd" in content
    assert str(DEFAULT_PORT) in content


def test_launchd_plist_contains_log_paths(backend: LaunchdBackend) -> None:
    content = backend.plist_content()
    assert "voxd-stdout.log" in content
    assert "voxd-stderr.log" in content


def test_launchd_plist_log_paths_use_current_user_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Log paths in the plist come from the invoking user's ``$HOME``."""
    fake_home = tmp_path / "Users" / "deploy"
    fake_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))

    be = LaunchdBackend(
        ProcessManager(),
        lambda: ["/usr/local/bin/voxd", "--port", "8421"],
    )
    content = be.plist_content()
    expected_stdout = str(fake_home / ".punt-labs" / "vox" / "logs" / "voxd-stdout.log")
    expected_stderr = str(fake_home / ".punt-labs" / "vox" / "logs" / "voxd-stderr.log")
    assert expected_stdout in content
    assert expected_stderr in content


def test_launchd_plist_keepalive(backend: LaunchdBackend) -> None:
    content = backend.plist_content()
    assert "<key>KeepAlive</key>" in content
    assert "<true/>" in content


def test_launchd_plist_has_process_type_interactive(backend: LaunchdBackend) -> None:
    """ProcessType=Interactive prevents App Nap throttling."""
    content = backend.plist_content()
    assert "<key>ProcessType</key>" in content
    assert "<string>Interactive</string>" in content


def test_launchd_plist_no_username_key(backend: LaunchdBackend) -> None:
    """LaunchAgents must not contain UserName -- invalid for agents."""
    content = backend.plist_content()
    assert "<key>UserName</key>" not in content


@patch.dict("os.environ", {"PATH": "/opt/homebrew/bin:/usr/bin:/bin"})
def test_launchd_plist_contains_path_from_env() -> None:
    be = LaunchdBackend(
        ProcessManager(),
        lambda: ["/opt/homebrew/bin/voxd", "--port", "8421"],
    )
    content = be.plist_content()
    assert "<key>EnvironmentVariables</key>" in content
    assert "<key>PATH</key>" in content
    assert "/opt/homebrew/bin:/usr/bin:/bin" in content


@patch("punt_vox.service.launchd.subprocess.run")
@patch("punt_vox.service.launchd._LAUNCHD_PLIST")
def test_launchd_stop_noop_when_plist_missing(
    mock_plist: MagicMock,
    mock_run: MagicMock,
) -> None:
    """Fresh install (no prior plist): stop skips the bootout call."""
    mock_plist.exists.return_value = False
    be = LaunchdBackend(ProcessManager(), list)
    be.stop()
    mock_run.assert_not_called()


@patch("punt_vox.service.launchd.subprocess.run")
@patch("punt_vox.service.launchd._LAUNCHD_PLIST")
def test_launchd_stop_bootout_when_plist_present(
    mock_plist: MagicMock,
    mock_run: MagicMock,
) -> None:
    """Existing plist: stop issues launchctl bootout (no sudo)."""
    mock_plist.exists.return_value = True
    mock_run.return_value = MagicMock(returncode=0)

    be = LaunchdBackend(ProcessManager(), list)
    be.stop()

    mock_run.assert_called_once()
    call_args = mock_run.call_args
    cmd = call_args[0][0]
    assert cmd[0] == "launchctl"
    assert cmd[1] == "bootout"
    assert "sudo" not in cmd
    assert call_args[1]["check"] is False


# ---------------------------------------------------------------------------
# install (fresh, no migration)
# ---------------------------------------------------------------------------


@patch("punt_vox.service.launchd.subprocess.run")
def test_launchd_install_no_sudo(
    mock_run: MagicMock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fresh install uses zero sudo calls -- bootstrap + kickstart only."""
    fake_home = tmp_path / "home" / "jfreeman"
    fake_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))

    # Patch _LAUNCHD_DIR so it uses a temp directory
    agents_dir = fake_home / "Library" / "LaunchAgents"
    monkeypatch.setattr("punt_vox.service.launchd._LAUNCHD_DIR", agents_dir)
    monkeypatch.setattr(
        "punt_vox.service.launchd._LAUNCHD_PLIST",
        agents_dir / "com.punt-labs.voxd.plist",
    )

    mock_run.return_value = MagicMock(returncode=0)

    be = LaunchdBackend(
        ProcessManager(),
        lambda: ["/usr/local/bin/voxd", "--port", "8421"],
    )
    be.install()

    # Verify no sudo in any call
    for call in mock_run.call_args_list:
        cmd = call[0][0]
        assert cmd[0] != "sudo", f"Unexpected sudo call: {cmd}"

    # Should have bootstrap + kickstart = 2 calls
    assert len(mock_run.call_args_list) == 2
    cmds = [c[0][0] for c in mock_run.call_args_list]
    assert cmds[0][1] == "bootstrap"
    assert cmds[1][1] == "kickstart"


@patch("punt_vox.service.launchd.subprocess.run")
def test_launchd_install_uses_gui_domain(
    mock_run: MagicMock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bootstrap and kickstart target gui/<uid> domain."""
    fake_home = tmp_path / "home" / "jfreeman"
    fake_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))

    agents_dir = fake_home / "Library" / "LaunchAgents"
    monkeypatch.setattr("punt_vox.service.launchd._LAUNCHD_DIR", agents_dir)
    monkeypatch.setattr(
        "punt_vox.service.launchd._LAUNCHD_PLIST",
        agents_dir / "com.punt-labs.voxd.plist",
    )

    mock_run.return_value = MagicMock(returncode=0)

    be = LaunchdBackend(
        ProcessManager(),
        lambda: ["/usr/local/bin/voxd", "--port", "8421"],
    )
    be.install()

    uid = os.getuid()
    bootstrap_cmd = mock_run.call_args_list[0][0][0]
    assert bootstrap_cmd[2] == f"gui/{uid}"

    kickstart_cmd = mock_run.call_args_list[1][0][0]
    assert kickstart_cmd[3] == f"gui/{uid}/com.punt-labs.voxd"


# ---------------------------------------------------------------------------
# migrate_from_daemon
# ---------------------------------------------------------------------------


@patch("punt_vox.service.launchd.subprocess.run")
def test_migrate_from_daemon_uses_exactly_two_sudo_calls(
    mock_run: MagicMock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Migration: sudo launchctl unload + sudo rm = 2 sudo calls."""
    fake_home = tmp_path / "home" / "jfreeman"
    fake_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))

    agents_dir = fake_home / "Library" / "LaunchAgents"
    monkeypatch.setattr("punt_vox.service.launchd._LAUNCHD_DIR", agents_dir)
    monkeypatch.setattr(
        "punt_vox.service.launchd._LAUNCHD_PLIST",
        agents_dir / "com.punt-labs.voxd.plist",
    )

    mock_run.return_value = MagicMock(returncode=0)
    mock_client = MagicMock()
    mock_client.health.return_value = {"status": "ok"}

    be = LaunchdBackend(
        ProcessManager(),
        lambda: ["/usr/local/bin/voxd", "--port", "8421"],
    )
    with (
        patch.object(ProcessManager, "ensure_port_free"),
        patch("punt_vox.client.VoxClientSync", return_value=mock_client),
    ):
        be.migrate_from_daemon()

    sudo_calls = [c for c in mock_run.call_args_list if c[0][0][0] == "sudo"]
    assert len(sudo_calls) == 2, (
        f"Expected 2 sudo calls, got {len(sudo_calls)}: {[c[0][0] for c in sudo_calls]}"
    )
    # First sudo: unload old LaunchDaemon
    assert sudo_calls[0][0][0][:3] == ["sudo", "launchctl", "unload"]
    assert str(_OLD_LAUNCHD_PLIST) in sudo_calls[0][0][0]
    # Second sudo: rm old plist
    assert sudo_calls[1][0][0][:2] == ["sudo", "rm"]
    assert str(_OLD_LAUNCHD_PLIST) in sudo_calls[1][0][0]


@patch("punt_vox.service.launchd.subprocess.run")
def test_migrate_from_daemon_bootstrap_before_rm(
    mock_run: MagicMock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Migration: new agent is bootstrapped before old plist is removed."""
    fake_home = tmp_path / "home" / "jfreeman"
    fake_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))

    agents_dir = fake_home / "Library" / "LaunchAgents"
    monkeypatch.setattr("punt_vox.service.launchd._LAUNCHD_DIR", agents_dir)
    monkeypatch.setattr(
        "punt_vox.service.launchd._LAUNCHD_PLIST",
        agents_dir / "com.punt-labs.voxd.plist",
    )

    mock_run.return_value = MagicMock(returncode=0)
    mock_client = MagicMock()
    mock_client.health.return_value = {"status": "ok"}

    be = LaunchdBackend(
        ProcessManager(),
        lambda: ["/usr/local/bin/voxd", "--port", "8421"],
    )
    with (
        patch.object(ProcessManager, "ensure_port_free"),
        patch("punt_vox.client.VoxClientSync", return_value=mock_client),
    ):
        be.migrate_from_daemon()

    cmds = [c[0][0] for c in mock_run.call_args_list]
    # Order: unload old, bootout new (pre-flight), bootstrap new, kickstart, rm old
    assert cmds[0][:3] == ["sudo", "launchctl", "unload"]
    assert cmds[1][1] == "bootout"
    assert cmds[2][1] == "bootstrap"
    assert cmds[3][1] == "kickstart"
    assert cmds[4][:2] == ["sudo", "rm"]
