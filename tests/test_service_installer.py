"""Tests for punt_vox.service.installer — top-level install/uninstall."""
# pyright: reportUnknownLambdaType=false

from __future__ import annotations

import stat as stat_mod
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from punt_vox.service.installer import ServiceInstaller
from punt_vox.service.launchd import LaunchdBackend
from punt_vox.service.process import ProcessManager
from punt_vox.service.systemd import SystemdBackend

# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------


@patch("punt_vox.service.installer.platform.system", return_value="Darwin")
def test_detect_platform_macos(_mock: MagicMock) -> None:
    assert ServiceInstaller.detect_platform() == "macos"


@patch("punt_vox.service.installer.platform.system", return_value="Linux")
def test_detect_platform_linux(_mock: MagicMock) -> None:
    assert ServiceInstaller.detect_platform() == "linux"


@patch("punt_vox.service.installer.platform.system", return_value="Windows")
def test_detect_platform_unsupported(_mock: MagicMock) -> None:
    with pytest.raises(SystemExit):
        ServiceInstaller.detect_platform()


# ---------------------------------------------------------------------------
# _ensure_user_dirs
# ---------------------------------------------------------------------------


def test_ensure_user_dirs_creates_tree_under_current_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_ensure_user_dirs creates the tree and returns the state root."""
    fake_home = tmp_path / "home" / "testuser"
    fake_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))

    inst = ServiceInstaller()
    result = inst._ensure_user_dirs()
    expected = fake_home / ".punt-labs" / "vox"
    assert result == expected
    assert expected.is_dir()
    assert (expected / "logs").is_dir()
    assert (expected / "run").is_dir()
    assert (expected / "cache").is_dir()


# ---------------------------------------------------------------------------
# install() — helpers
# ---------------------------------------------------------------------------


def _setup_fake_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Set up fake HOME, voxd binary, and sys.executable for install tests."""
    fake_home = tmp_path / "home" / "user"
    fake_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "voxd").write_text("#!/bin/sh\n")
    (bin_dir / "voxd").chmod(0o755)
    (bin_dir / "python").write_text("#!/bin/sh\n")
    (bin_dir / "python").chmod(0o755)
    monkeypatch.setattr(
        "punt_vox.service.installer.sys.executable", str(bin_dir / "python")
    )

    monkeypatch.setattr("punt_vox.service.installer.os.geteuid", lambda: 1000)

    # Ensure migration path is not triggered by a real old LaunchDaemon plist.
    monkeypatch.setattr(
        "punt_vox.service.installer._OLD_LAUNCHD_PLIST",
        tmp_path / "no-such-old-plist",
    )

    return fake_home


# ---------------------------------------------------------------------------
# install() — end-to-end under a tmp HOME
# ---------------------------------------------------------------------------


@patch.object(LaunchdBackend, "status", return_value=True)
@patch.object(LaunchdBackend, "install")
@patch.object(LaunchdBackend, "stop")
@patch.object(SystemdBackend, "status", return_value=True)
@patch.object(SystemdBackend, "install")
@patch.object(SystemdBackend, "stop")
@patch.object(SystemdBackend, "cleanup_stale_user_unit", return_value=False)
@patch.object(ProcessManager, "ensure_port_free")
def test_install_runs_as_user_creates_keys_env(
    _mock_port: MagicMock,
    _mock_cleanup: MagicMock,
    _mock_sd_stop: MagicMock,
    _mock_sd_install: MagicMock,
    _mock_sd_status: MagicMock,
    _mock_ld_stop: MagicMock,
    _mock_ld_install: MagicMock,
    _mock_ld_status: MagicMock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """install() writes keys.env under the current user's home, mode 0600."""
    fake_home = _setup_fake_env(tmp_path, monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-openai")
    monkeypatch.setenv("TTS_PROVIDER", "openai")

    inst = ServiceInstaller()
    inst.install()

    keys_path = fake_home / ".punt-labs" / "vox" / "keys.env"
    assert keys_path.exists()
    mode = stat_mod.S_IMODE(keys_path.stat().st_mode)
    assert mode == 0o600
    content = keys_path.read_text()
    assert "OPENAI_API_KEY=sk-test-openai" in content
    assert "TTS_PROVIDER=openai" in content


@patch.object(LaunchdBackend, "status", return_value=False)
@patch.object(LaunchdBackend, "install")
@patch.object(LaunchdBackend, "stop")
@patch.object(ProcessManager, "ensure_port_free")
@patch.object(ServiceInstaller, "detect_platform", return_value="macos")
def test_install_reports_not_running(
    _mock_platform: MagicMock,
    _mock_port: MagicMock,
    _mock_ld_stop: MagicMock,
    _mock_ld_install: MagicMock,
    _mock_ld_status: MagicMock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """install() reports 'not yet running' when the service is down."""
    _setup_fake_env(tmp_path, monkeypatch)

    inst = ServiceInstaller()
    result = inst.install()
    assert "not yet running" in result


@patch.object(LaunchdBackend, "status", return_value=True)
@patch.object(LaunchdBackend, "install")
@patch.object(LaunchdBackend, "stop")
@patch.object(SystemdBackend, "status", return_value=True)
@patch.object(SystemdBackend, "install")
@patch.object(SystemdBackend, "stop")
@patch.object(SystemdBackend, "cleanup_stale_user_unit", return_value=False)
@patch.object(ProcessManager, "ensure_port_free")
def test_install_does_not_chown_anything(
    _mock_port: MagicMock,
    _mock_cleanup: MagicMock,
    _mock_sd_stop: MagicMock,
    _mock_sd_install: MagicMock,
    _mock_sd_status: MagicMock,
    _mock_ld_stop: MagicMock,
    _mock_ld_install: MagicMock,
    _mock_ld_status: MagicMock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """install() must never invoke os.chown / os.lchown / os.fchown."""
    _setup_fake_env(tmp_path, monkeypatch)

    chown_calls: list[tuple[str, object, int, int]] = []

    def _record_chown(path: object, uid: int, gid: int) -> None:
        chown_calls.append(("chown", path, uid, gid))

    def _record_lchown(path: object, uid: int, gid: int) -> None:
        chown_calls.append(("lchown", path, uid, gid))

    def _record_fchown(fd: int, uid: int, gid: int) -> None:
        chown_calls.append(("fchown", fd, uid, gid))

    monkeypatch.setattr("os.chown", _record_chown)
    monkeypatch.setattr("os.lchown", _record_lchown)
    monkeypatch.setattr("os.fchown", _record_fchown)

    inst = ServiceInstaller()
    inst.install()

    assert chown_calls == []


# ---------------------------------------------------------------------------
# install() — pre-flight stop before port check
# ---------------------------------------------------------------------------


def test_install_runs_systemd_stop_before_port_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """install() must stop voxd via systemctl BEFORE the port check."""
    _setup_fake_env(tmp_path, monkeypatch)

    call_order: list[str] = []

    monkeypatch.setattr(
        ServiceInstaller,
        "detect_platform",
        staticmethod(lambda: "linux"),
    )
    monkeypatch.setattr(
        SystemdBackend,
        "cleanup_stale_user_unit",
        lambda self: call_order.append("cleanup") or False,  # type: ignore[func-returns-value]
    )
    monkeypatch.setattr(
        SystemdBackend,
        "stop",
        lambda self: call_order.append("systemd_stop"),
    )
    monkeypatch.setattr(
        ProcessManager,
        "ensure_port_free",
        lambda self: call_order.append("ensure_port_free"),
    )
    monkeypatch.setattr(SystemdBackend, "install", lambda self, user: None)
    monkeypatch.setattr(SystemdBackend, "status", lambda self: True)

    inst = ServiceInstaller()
    inst.install()

    assert "systemd_stop" in call_order
    assert "ensure_port_free" in call_order
    idx_stop = call_order.index("systemd_stop")
    idx_port = call_order.index("ensure_port_free")
    assert idx_stop < idx_port


def test_install_runs_launchd_stop_before_port_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """install() must unload voxd from launchd BEFORE the port check."""
    _setup_fake_env(tmp_path, monkeypatch)

    call_order: list[str] = []

    monkeypatch.setattr(
        ServiceInstaller,
        "detect_platform",
        staticmethod(lambda: "macos"),
    )
    monkeypatch.setattr(
        LaunchdBackend,
        "stop",
        lambda self: call_order.append("launchd_stop"),
    )
    monkeypatch.setattr(
        ProcessManager,
        "ensure_port_free",
        lambda self: call_order.append("ensure_port_free"),
    )
    monkeypatch.setattr(LaunchdBackend, "install", lambda self: None)
    monkeypatch.setattr(LaunchdBackend, "status", lambda self: True)

    inst = ServiceInstaller()
    inst.install()

    assert "launchd_stop" in call_order
    assert "ensure_port_free" in call_order
    idx_stop = call_order.index("launchd_stop")
    idx_port = call_order.index("ensure_port_free")
    assert idx_stop < idx_port


# ---------------------------------------------------------------------------
# install() — refuses to run as root
# ---------------------------------------------------------------------------


def test_install_refuses_to_run_as_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """install() raises SystemExit when ``os.geteuid() == 0``."""
    fake_home = tmp_path / "home" / "user"
    fake_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "voxd").write_text("#!/bin/sh\n")
    (bin_dir / "voxd").chmod(0o755)
    (bin_dir / "python").write_text("#!/bin/sh\n")
    (bin_dir / "python").chmod(0o755)
    monkeypatch.setattr(
        "punt_vox.service.installer.sys.executable", str(bin_dir / "python")
    )

    monkeypatch.setattr("punt_vox.service.installer.os.geteuid", lambda: 0)

    inst = ServiceInstaller()
    with pytest.raises(SystemExit, match="without sudo"):
        inst.install()

    assert not (fake_home / ".punt-labs").exists()


# ---------------------------------------------------------------------------
# install() — legacy cleanup ordering
# ---------------------------------------------------------------------------


def test_install_cleans_stale_user_unit_before_systemd_stop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """install() must run the legacy cleanup before ``_systemd_stop``."""
    _setup_fake_env(tmp_path, monkeypatch)

    call_order: list[str] = []

    monkeypatch.setattr(
        ServiceInstaller,
        "detect_platform",
        staticmethod(lambda: "linux"),
    )
    monkeypatch.setattr(
        SystemdBackend,
        "cleanup_stale_user_unit",
        lambda self: call_order.append("cleanup_stale_user_unit") or False,  # type: ignore[func-returns-value]
    )
    monkeypatch.setattr(
        SystemdBackend,
        "stop",
        lambda self: call_order.append("systemd_stop"),
    )
    monkeypatch.setattr(
        ProcessManager,
        "ensure_port_free",
        lambda self: call_order.append("ensure_port_free"),
    )
    monkeypatch.setattr(SystemdBackend, "install", lambda self, user: None)
    monkeypatch.setattr(SystemdBackend, "status", lambda self: True)

    inst = ServiceInstaller()
    inst.install()

    assert call_order == [
        "cleanup_stale_user_unit",
        "systemd_stop",
        "ensure_port_free",
    ], f"unexpected install() ordering: {call_order}"
