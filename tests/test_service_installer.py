"""Tests for punt_vox.service.installer — top-level install/uninstall."""
# pyright: reportUnknownLambdaType=false

from __future__ import annotations

import ipaddress
import stat as stat_mod
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from punt_vox.client import VoxdConnectionError, VoxdProtocolError
from punt_vox.service.installer import (
    ServiceInstaller,
    _HealthTarget,  # pyright: ignore[reportPrivateUsage]
)
from punt_vox.service.launchd import LaunchdBackend
from punt_vox.service.process import DEFAULT_PORT, ProcessManager
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
    *,
    bypass_health: bool = True,
) -> Path:
    """Set up fake HOME, voxd binary, and sys.executable for install tests.

    ``bypass_health`` stubs the post-install health poll to a no-op so that
    wiring/ordering tests do not attempt a real socket connection. The
    health boundary itself is exercised by the dedicated tests below, which
    pass ``bypass_health=False``.
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
    monkeypatch.setattr(
        "punt_vox.service.installer.sys.executable", str(bin_dir / "python")
    )

    monkeypatch.setattr("punt_vox.service.installer.os.geteuid", lambda: 1000)

    if bypass_health:
        monkeypatch.setattr(
            ServiceInstaller,
            "_verify_serving",
            staticmethod(lambda platform, service_path: None),
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


# ---------------------------------------------------------------------------
# install() — post-install health verification (silent-down guard)
# ---------------------------------------------------------------------------


@patch.object(LaunchdBackend, "status", return_value=True)
@patch.object(LaunchdBackend, "install")
@patch.object(LaunchdBackend, "stop")
@patch.object(ProcessManager, "ensure_port_free")
@patch.object(ServiceInstaller, "detect_platform", return_value="macos")
def test_install_reports_running_when_daemon_healthy(
    _mock_platform: MagicMock,
    _mock_port: MagicMock,
    _mock_ld_stop: MagicMock,
    _mock_ld_install: MagicMock,
    _mock_ld_status: MagicMock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A registered daemon that answers health is reported 'running'."""
    _setup_fake_env(tmp_path, monkeypatch, bypass_health=False)

    healthy = MagicMock()
    healthy.health.return_value = {"status": "ok"}
    monkeypatch.setattr(
        "punt_vox.service.installer.VoxClientSync",
        lambda **_kwargs: healthy,
    )

    inst = ServiceInstaller()
    result = inst.install()

    healthy.health.assert_called_once()
    assert "running" in result
    assert "not yet running" not in result


@patch.object(LaunchdBackend, "status", return_value=True)
@patch.object(LaunchdBackend, "install")
@patch.object(LaunchdBackend, "stop")
@patch.object(ProcessManager, "ensure_port_free")
@patch.object(ServiceInstaller, "detect_platform", return_value="macos")
def test_install_raises_when_daemon_never_healthy(
    _mock_platform: MagicMock,
    _mock_port: MagicMock,
    _mock_ld_stop: MagicMock,
    _mock_ld_install: MagicMock,
    _mock_ld_status: MagicMock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A registered daemon that never serves fails the install loudly.

    ``launchctl`` registration succeeds but voxd dies on startup, so the
    health poll exhausts its deadline and ``install()`` raises rather than
    reporting a false 'running' -- the silent-down regression guard.
    """
    _setup_fake_env(tmp_path, monkeypatch, bypass_health=False)
    # Shrink the deadline and drop the sleep so the poll exhausts instantly.
    monkeypatch.setattr("punt_vox.service.installer._HEALTH_DEADLINE_S", 0.05)
    monkeypatch.setattr("punt_vox.service.installer.time.sleep", lambda _s: None)

    down = MagicMock()
    down.health.side_effect = VoxdConnectionError("connection refused")
    monkeypatch.setattr(
        "punt_vox.service.installer.VoxClientSync",
        lambda **_kwargs: down,
    )

    inst = ServiceInstaller()
    with pytest.raises(RuntimeError, match="never became reachable"):
        inst.install()

    assert down.health.called


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


# ---------------------------------------------------------------------------
# _HealthTarget — host/port derivation for the post-install poll
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("platform", ["macos", "linux"])
def test_health_target_pins_installed_default_port(
    platform: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The poll port is DEFAULT_PORT regardless of a stray VOXD_PORT env."""
    monkeypatch.setenv("VOXD_PORT", "9999")
    assert _HealthTarget(platform).port == DEFAULT_PORT


@pytest.mark.parametrize("platform", ["macos", "linux"])
def test_health_target_unset_bind_maps_to_loopback(
    platform: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unset VOXD_BIND resolves the loopback health host on both backends."""
    monkeypatch.delenv("VOXD_BIND", raising=False)
    assert _HealthTarget(platform).host == "127.0.0.1"


# Construct the unspecified addresses rather than spelling "0.0.0.0" as a
# literal, which trips ruff S104 (bind-all-interfaces) even in a test value.
_IPV4_WILDCARD = str(ipaddress.IPv4Address(0))
_IPV6_WILDCARD = str(ipaddress.IPv6Address(0))


@pytest.mark.parametrize("platform", ["macos", "linux"])
@pytest.mark.parametrize(
    "wildcard",
    [_IPV4_WILDCARD, _IPV6_WILDCARD, f"  {_IPV4_WILDCARD}  "],
)
def test_health_target_wildcard_bind_maps_to_loopback(
    wildcard: str,
    platform: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wildcard (unspecified) binds resolve loopback — voxd accepts it there."""
    monkeypatch.setenv("VOXD_BIND", wildcard)
    assert _HealthTarget(platform).host == "127.0.0.1"


@pytest.mark.parametrize("platform", ["macos", "linux"])
def test_health_target_concrete_bind_used_directly(
    platform: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A concrete, safe bind is polled directly on both backends."""
    monkeypatch.setenv("VOXD_BIND", "192.168.1.50")
    assert _HealthTarget(platform).host == "192.168.1.50"


@pytest.mark.parametrize("platform", ["macos", "linux"])
def test_health_target_hostname_bind_used_directly(
    platform: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-IP bind value (hostname) is polled as given, not loopback."""
    monkeypatch.setenv("VOXD_BIND", "voxd.internal")
    assert _HealthTarget(platform).host == "voxd.internal"


def test_health_target_systemd_rejected_bind_maps_to_loopback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A VOXD_BIND rejected by safe_systemd_value resolves to loopback (linux).

    ``SystemdBackend.unit_content`` embeds ``VOXD_BIND`` only when
    ``safe_systemd_value`` accepts it. A trailing newline is rejected, so the
    unit drops the variable and voxd binds its DEFAULT_HOST loopback. The
    health host must match that -- 127.0.0.1 -- not the rejected raw address,
    or the poll false-fails a healthy daemon.
    """
    monkeypatch.setenv("VOXD_BIND", "192.168.1.50\n")
    assert not SystemdBackend.safe_systemd_value("192.168.1.50\n")
    assert _HealthTarget("linux").host == "127.0.0.1"


def test_health_target_launchd_does_not_apply_systemd_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """launchd embeds VOXD_BIND verbatim, so the systemd gate must not apply.

    The same value the systemd gate rejects is passed through by launchd,
    which has no safety gate. The health host must therefore resolve the
    address (whitespace-normalized), not loopback -- the resolution diverges
    by backend exactly as the units do.
    """
    monkeypatch.setenv("VOXD_BIND", "192.168.1.50\n")
    assert _HealthTarget("macos").host == "192.168.1.50"


# ---------------------------------------------------------------------------
# install() — health poll targets the exact installed daemon
# ---------------------------------------------------------------------------


@patch.object(LaunchdBackend, "status", return_value=True)
@patch.object(LaunchdBackend, "install")
@patch.object(LaunchdBackend, "stop")
@patch.object(ProcessManager, "ensure_port_free")
@patch.object(ServiceInstaller, "detect_platform", return_value="macos")
def test_install_health_poll_pins_installed_port_over_env(
    _mock_platform: MagicMock,
    _mock_port: MagicMock,
    _mock_ld_stop: MagicMock,
    _mock_ld_install: MagicMock,
    _mock_ld_status: MagicMock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The health poll targets DEFAULT_PORT even when VOXD_PORT points elsewhere.

    A stray VOXD_PORT in the install environment must not redirect the poll
    to a different daemon than the one the service unit was started with.
    """
    _setup_fake_env(tmp_path, monkeypatch, bypass_health=False)
    monkeypatch.setenv("VOXD_PORT", "9999")
    monkeypatch.setenv("VOXD_BIND", "192.168.1.50")

    captured: list[dict[str, object]] = []

    def _factory(**kwargs: object) -> MagicMock:
        captured.append(kwargs)
        client = MagicMock()
        client.health.return_value = {"status": "ok"}
        return client

    monkeypatch.setattr("punt_vox.service.installer.VoxClientSync", _factory)

    inst = ServiceInstaller()
    inst.install()

    assert captured, "VoxClientSync was never constructed"
    assert captured[0]["port"] == DEFAULT_PORT
    assert captured[0]["host"] == "192.168.1.50"


@patch.object(LaunchdBackend, "status", return_value=True)
@patch.object(LaunchdBackend, "install")
@patch.object(LaunchdBackend, "stop")
@patch.object(ProcessManager, "ensure_port_free")
@patch.object(ServiceInstaller, "detect_platform", return_value="macos")
def test_install_health_poll_retries_transient_protocol_error(
    _mock_platform: MagicMock,
    _mock_port: MagicMock,
    _mock_ld_stop: MagicMock,
    _mock_ld_install: MagicMock,
    _mock_ld_status: MagicMock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A transient VoxdProtocolError is retried, not fatal.

    A receive timeout while voxd is still binding its port surfaces as
    VoxdProtocolError. The poll must sleep and retry until the daemon
    answers rather than failing the install on the first hiccup.
    """
    _setup_fake_env(tmp_path, monkeypatch, bypass_health=False)
    monkeypatch.setattr("punt_vox.service.installer.time.sleep", lambda _s: None)

    client = MagicMock()
    client.health.side_effect = [
        VoxdProtocolError("timeout waiting for response to 'health'"),
        {"status": "ok"},
    ]
    monkeypatch.setattr(
        "punt_vox.service.installer.VoxClientSync",
        lambda **_kwargs: client,
    )

    inst = ServiceInstaller()
    result = inst.install()

    assert client.health.call_count == 2
    assert "running" in result
    assert "not yet running" not in result


# ---------------------------------------------------------------------------
# install() — health poll auth token comes from the run file, not the shell
# ---------------------------------------------------------------------------


def _write_run_token(fake_home: Path, token: str) -> None:
    """Write ``serve.token`` under the install's run dir so the poll reads it."""
    run_dir = fake_home / ".punt-labs" / "vox" / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "serve.token").write_text(token)


@patch.object(LaunchdBackend, "status", return_value=True)
@patch.object(LaunchdBackend, "install")
@patch.object(LaunchdBackend, "stop")
@patch.object(ProcessManager, "ensure_port_free")
@patch.object(ServiceInstaller, "detect_platform", return_value="macos")
def test_install_health_poll_uses_run_file_token_not_shell_env(
    _mock_platform: MagicMock,
    _mock_port: MagicMock,
    _mock_ld_stop: MagicMock,
    _mock_ld_install: MagicMock,
    _mock_ld_status: MagicMock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The health client authenticates with serve.token, not a stray VOXD_TOKEN.

    ``VoxClientSync``'s normal resolution prefers a shell ``VOXD_TOKEN`` over
    the run file. Passing the run-file token explicitly means a stale/wrong
    ``VOXD_TOKEN`` in the install environment cannot redirect auth and
    false-fail a healthy daemon.
    """
    fake_home = _setup_fake_env(tmp_path, monkeypatch, bypass_health=False)
    monkeypatch.setenv("VOXD_TOKEN", "stray-shell-token")
    _write_run_token(fake_home, "authoritative-run-token")

    captured: list[dict[str, object]] = []

    def _factory(**kwargs: object) -> MagicMock:
        captured.append(kwargs)
        client = MagicMock()
        client.health.return_value = {"status": "ok"}
        return client

    monkeypatch.setattr("punt_vox.service.installer.VoxClientSync", _factory)

    inst = ServiceInstaller()
    inst.install()

    assert captured, "VoxClientSync was never constructed"
    # The run-file token wins; the stray shell VOXD_TOKEN never reaches auth.
    assert captured[0]["token"] == "authoritative-run-token"


@patch.object(LaunchdBackend, "status", return_value=True)
@patch.object(LaunchdBackend, "install")
@patch.object(LaunchdBackend, "stop")
@patch.object(ProcessManager, "ensure_port_free")
@patch.object(ServiceInstaller, "detect_platform", return_value="macos")
def test_install_health_poll_token_none_when_run_file_absent(
    _mock_platform: MagicMock,
    _mock_port: MagicMock,
    _mock_ld_stop: MagicMock,
    _mock_ld_install: MagicMock,
    _mock_ld_status: MagicMock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no serve.token yet, the health client gets token=None, not VOXD_TOKEN.

    voxd writes serve.token on startup; an early probe may see no file. The
    client must pass ``None`` (bypassing auth for the retry) rather than fall
    back to a stray shell ``VOXD_TOKEN``. The poll's retry loop tolerates the
    brief race until the run file appears.
    """
    _setup_fake_env(tmp_path, monkeypatch, bypass_health=False)
    monkeypatch.setenv("VOXD_TOKEN", "stray-shell-token")

    captured: list[dict[str, object]] = []

    def _factory(**kwargs: object) -> MagicMock:
        captured.append(kwargs)
        client = MagicMock()
        client.health.return_value = {"status": "ok"}
        return client

    monkeypatch.setattr("punt_vox.service.installer.VoxClientSync", _factory)

    inst = ServiceInstaller()
    inst.install()

    assert captured, "VoxClientSync was never constructed"
    assert captured[0]["token"] is None
