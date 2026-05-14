"""Tests for punt_vox.service.launchd — macOS launchd backend."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from punt_vox.service import DEFAULT_PORT
from punt_vox.service.launchd import LaunchdBackend
from punt_vox.service.process import ProcessManager


@pytest.fixture()
def backend(monkeypatch: pytest.MonkeyPatch) -> LaunchdBackend:
    """Create a LaunchdBackend with a stub exec-args function."""
    return LaunchdBackend(
        ProcessManager(),
        lambda: ["/usr/local/bin/voxd", "--port", "8421"],
    )


# ---------------------------------------------------------------------------
# launchd plist content
# ---------------------------------------------------------------------------


def test_launchd_plist_contains_label(backend: LaunchdBackend) -> None:
    content = backend.plist_content("testuser")
    assert "com.punt-labs.voxd" in content


def test_launchd_plist_contains_args(backend: LaunchdBackend) -> None:
    content = backend.plist_content("testuser")
    assert "voxd" in content
    assert str(DEFAULT_PORT) in content


def test_launchd_plist_contains_log_paths(backend: LaunchdBackend) -> None:
    content = backend.plist_content("testuser")
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
    content = be.plist_content("deploy")
    expected_stdout = str(fake_home / ".punt-labs" / "vox" / "logs" / "voxd-stdout.log")
    expected_stderr = str(fake_home / ".punt-labs" / "vox" / "logs" / "voxd-stderr.log")
    assert expected_stdout in content
    assert expected_stderr in content


def test_launchd_plist_keepalive(backend: LaunchdBackend) -> None:
    content = backend.plist_content("testuser")
    assert "<key>KeepAlive</key>" in content
    assert "<true/>" in content


@patch.dict("os.environ", {"PATH": "/opt/homebrew/bin:/usr/bin:/bin"})
def test_launchd_plist_contains_path_from_env() -> None:
    be = LaunchdBackend(
        ProcessManager(),
        lambda: ["/opt/homebrew/bin/voxd", "--port", "8421"],
    )
    content = be.plist_content("testuser")
    assert "<key>EnvironmentVariables</key>" in content
    assert "<key>PATH</key>" in content
    assert "/opt/homebrew/bin:/usr/bin:/bin" in content


# ---------------------------------------------------------------------------
# _launchd_stop / _launchd_install
# ---------------------------------------------------------------------------


@patch("punt_vox.service.launchd.subprocess.run")
@patch("punt_vox.service.launchd._LAUNCHD_PLIST")
def test_launchd_stop_noop_when_plist_missing(
    mock_plist: MagicMock,
    mock_run: MagicMock,
) -> None:
    """Fresh install (no prior plist): stop skips the sudo call."""
    mock_plist.exists.return_value = False
    be = LaunchdBackend(ProcessManager(), list)
    be.stop()
    mock_run.assert_not_called()


@patch("punt_vox.service.launchd.subprocess.run")
@patch("punt_vox.service.launchd._LAUNCHD_PLIST")
def test_launchd_stop_unloads_when_plist_present(
    mock_plist: MagicMock,
    mock_run: MagicMock,
) -> None:
    """Existing plist: stop issues sudo launchctl unload -w."""
    mock_plist.exists.return_value = True
    mock_run.return_value = MagicMock(returncode=0)

    be = LaunchdBackend(ProcessManager(), list)
    be.stop()

    mock_run.assert_called_once()
    call_args = mock_run.call_args
    assert call_args[0][0][:4] == ["sudo", "launchctl", "unload", "-w"]
    assert call_args[1]["check"] is False


@patch("punt_vox.service.launchd.subprocess.run")
def test_launchd_install_invokes_expected_sudo_commands(
    mock_run: MagicMock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``install`` issues three sudo subprocess calls in order."""
    fake_home = tmp_path / "home" / "jfreeman"
    fake_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))
    (fake_home / ".punt-labs" / "vox").mkdir(parents=True)

    mock_run.return_value = MagicMock(returncode=0)

    be = LaunchdBackend(
        ProcessManager(),
        lambda: ["/usr/local/bin/voxd", "--port", "8421"],
    )
    be.install("jfreeman")

    sudo_calls = [c for c in mock_run.call_args_list if c[0][0][0] == "sudo"]
    assert len(sudo_calls) == 3, (
        f"Expected 3 sudo calls, got {len(sudo_calls)}: {[c[0][0] for c in sudo_calls]}"
    )
    assert sudo_calls[0][0][0][:2] == ["sudo", "install"]
    assert "/Library/LaunchDaemons/com.punt-labs.voxd.plist" in sudo_calls[0][0][0]
    assert sudo_calls[1][0][0][:3] == ["sudo", "launchctl", "load"]
    assert sudo_calls[2][0][0] == [
        "sudo",
        "launchctl",
        "kickstart",
        "-k",
        "system/com.punt-labs.voxd",
    ]


@patch("punt_vox.service.launchd.subprocess.run")
def test_launchd_install_restarts_already_running_voxd(
    mock_run: MagicMock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression guard: ``kickstart -k`` is called unconditionally."""
    fake_home = tmp_path / "home" / "jfreeman"
    fake_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))
    (fake_home / ".punt-labs" / "vox").mkdir(parents=True)

    mock_run.return_value = MagicMock(returncode=0)

    be = LaunchdBackend(
        ProcessManager(),
        lambda: ["/usr/local/bin/voxd", "--port", "8421"],
    )
    be.install("jfreeman")

    kickstart_calls = [
        c
        for c in mock_run.call_args_list
        if c[0][0][:4] == ["sudo", "launchctl", "kickstart", "-k"]
    ]
    assert len(kickstart_calls) == 1
