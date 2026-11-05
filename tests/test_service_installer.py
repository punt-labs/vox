"""Tests for punt_vox.service.installer — top-level install/uninstall."""
# pyright: reportUnknownLambdaType=false

from __future__ import annotations

import stat as stat_mod
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from punt_vox.client_errors import VoxdConnectionError, VoxdProtocolError
from punt_vox.service.backend import (
    BackendCommandError,
    PortProbeError,
    PrivilegeDeniedError,
)
from punt_vox.service.daemon_probe import DaemonProbe
from punt_vox.service.health_verify import HealthVerifier
from punt_vox.service.installer import ServiceInstaller
from punt_vox.service.launchd import LaunchdBackend
from punt_vox.service.process import DEFAULT_PORT, ProcessManager
from punt_vox.service.query_answer import QueryAnswer
from punt_vox.service.systemd import SystemdBackend
from punt_vox.types_health import HealthStatus

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
        monkeypatch.setattr(HealthVerifier, "verify", lambda self: _OK)

    return fake_home


def _fake_backend(
    monkeypatch: pytest.MonkeyPatch,
    cls: type[LaunchdBackend] | type[SystemdBackend],
    *,
    active: bool = True,
    installed: bool = False,
    bind: str = "",
) -> list[str]:
    """Stub *cls*'s service-manager calls; return the order they ran in."""
    calls: list[str] = []
    monkeypatch.setattr(cls, "is_installed", lambda self: installed)
    monkeypatch.setattr(cls, "request_stop", lambda self: calls.append("request_stop"))
    monkeypatch.setattr(cls, "install", lambda self, user: calls.append("install"))
    monkeypatch.setattr(cls, "is_active", lambda self: active)
    monkeypatch.setattr(
        cls, "is_active_within", lambda self, within_s: QueryAnswer.of(holds=active)
    )
    monkeypatch.setattr(cls, "installed_bind", lambda self: bind)
    monkeypatch.setattr(
        ProcessManager, "ensure_port_free", lambda self: calls.append("port_free")
    )
    return calls


def _on(monkeypatch: pytest.MonkeyPatch, plat: str) -> None:
    monkeypatch.setattr(ServiceInstaller, "detect_platform", staticmethod(lambda: plat))


def _visible_voxd(monkeypatch: pytest.MonkeyPatch) -> None:
    """A voxd holds 8421, so the gated health request may go out."""
    monkeypatch.setattr(
        DaemonProbe, "voxd_processes", lambda self: frozenset({MagicMock()})
    )
    monkeypatch.setattr("punt_vox.service.health_verify._HEALTH_DEADLINE_S", 0.05)


def _clients(
    monkeypatch: pytest.MonkeyPatch, answer: object
) -> list[dict[str, object]]:
    """Route every health client to one mock answering *answer* in turn."""
    captured: list[dict[str, object]] = []
    client = MagicMock()
    if isinstance(answer, list | BaseException):
        client.health.side_effect = answer
    else:
        client.health.return_value = answer

    def _factory(**kwargs: object) -> MagicMock:
        captured.append(kwargs)
        return client

    monkeypatch.setattr("punt_vox.service.health.VoxClientSync", _factory)
    return captured


_OK = HealthStatus(status="ok", pid=42, daemon_version="5.0.6")


# ---------------------------------------------------------------------------
# install() — end-to-end under a tmp HOME
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("plat", ["macos", "linux"])
def test_install_runs_as_user_creates_keys_env(
    plat: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """install() writes keys.env under the current user's home, mode 0600."""
    fake_home = _setup_fake_env(tmp_path, monkeypatch)
    _on(monkeypatch, plat)
    _fake_backend(monkeypatch, LaunchdBackend if plat == "macos" else SystemdBackend)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-openai")
    monkeypatch.setenv("TTS_PROVIDER", "openai")

    ServiceInstaller().install()

    keys_path = fake_home / ".punt-labs" / "vox" / "keys.env"
    assert keys_path.exists()
    mode = stat_mod.S_IMODE(keys_path.stat().st_mode)
    assert mode == 0o600
    content = keys_path.read_text()
    assert "OPENAI_API_KEY=sk-test-openai" in content
    assert "TTS_PROVIDER=openai" in content


def test_install_status_reports_skipped_keys_by_name_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Kept, refused, and empty keys reach the operator in the status output."""
    fake_home = _setup_fake_env(tmp_path, monkeypatch)
    _on(monkeypatch, "macos")
    _fake_backend(monkeypatch, LaunchdBackend)
    keys_path = fake_home / ".punt-labs" / "vox" / "keys.env"
    keys_path.parent.mkdir(parents=True)
    keys_path.write_text("ELEVENLABS_API_KEY=sk-stored-eleven\n")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "")
    monkeypatch.setenv("OPENAI_API_KEY", "sk bad")

    result = ServiceInstaller().install()

    assert (
        "ELEVENLABS_API_KEY: empty in the environment; kept the stored value" in result
    )
    assert "OPENAI_API_KEY: refused: a space at position 3" in result
    assert "its provider has no credential" in result
    assert "sk-stored-eleven" not in result
    assert "sk bad" not in result


def test_install_fails_when_the_unit_never_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An installed unit that is not active is a failed install."""
    _setup_fake_env(tmp_path, monkeypatch, bypass_health=False)
    _on(monkeypatch, "macos")
    _fake_backend(monkeypatch, LaunchdBackend, active=False)
    monkeypatch.setattr("punt_vox.service.health_verify._HEALTH_DEADLINE_S", 0.05)

    with pytest.raises(SystemExit, match="not active"):
        ServiceInstaller().install()


def test_install_launchctl_failure_is_a_clean_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A launchctl failure during install never escapes as a traceback."""
    _setup_fake_env(tmp_path, monkeypatch)
    _on(monkeypatch, "macos")
    _fake_backend(monkeypatch, LaunchdBackend)

    def broken(self: LaunchdBackend, user: str) -> None:
        raise BackendCommandError.of(["launchctl", "bootstrap"], 5, "I/O error")

    monkeypatch.setattr(LaunchdBackend, "install", broken)
    with pytest.raises(SystemExit, match="I/O error"):
        ServiceInstaller().install()


# ---------------------------------------------------------------------------
# install() — post-install health verification (silent-down guard)
# ---------------------------------------------------------------------------


def test_install_reports_running_when_daemon_healthy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A registered daemon that answers health is reported 'running'."""
    fake_home = _setup_fake_env(tmp_path, monkeypatch, bypass_health=False)
    _on(monkeypatch, "macos")
    _fake_backend(monkeypatch, LaunchdBackend)
    _visible_voxd(monkeypatch)
    _write_run_token(fake_home, "run-token")
    captured = _clients(monkeypatch, _OK)

    result = ServiceInstaller().install()

    assert len(captured) == 1
    assert "running" in result
    assert "not yet running" not in result


def test_install_raises_when_daemon_never_healthy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A registered daemon that never serves fails the install loudly.

    Registration succeeds but voxd dies on startup, so the health poll
    exhausts its deadline and ``install()`` raises rather than reporting a
    false 'running' -- the silent-down regression guard.
    """
    fake_home = _setup_fake_env(tmp_path, monkeypatch, bypass_health=False)
    _on(monkeypatch, "macos")
    _fake_backend(monkeypatch, LaunchdBackend)
    _visible_voxd(monkeypatch)
    _write_run_token(fake_home, "run-token")
    captured = _clients(monkeypatch, VoxdConnectionError("connection refused"))

    with pytest.raises(SystemExit, match="never became reachable"):
        ServiceInstaller().install()

    assert captured


def test_install_does_not_chown_anything(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """install() must never invoke os.chown / os.lchown / os.fchown."""
    _setup_fake_env(tmp_path, monkeypatch)
    _on(monkeypatch, "linux")
    _fake_backend(monkeypatch, SystemdBackend)

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

    ServiceInstaller().install()

    assert chown_calls == []


# ---------------------------------------------------------------------------
# install() — pre-flight stop through request_stop, before the port check
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("plat", ["macos", "linux"])
def test_install_requests_stop_before_port_check(
    plat: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A previous install is stopped through the manager before the port check.

    Otherwise launchd's KeepAlive respawns voxd the instant the cleanup kills it.
    """
    _setup_fake_env(tmp_path, monkeypatch)
    _on(monkeypatch, plat)
    cls = LaunchdBackend if plat == "macos" else SystemdBackend
    calls = _fake_backend(monkeypatch, cls, installed=True)

    ServiceInstaller().install()

    assert calls == ["request_stop", "port_free", "install"]


def test_install_fresh_host_makes_no_stop_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup_fake_env(tmp_path, monkeypatch)
    _on(monkeypatch, "linux")
    calls = _fake_backend(monkeypatch, SystemdBackend, installed=False)

    ServiceInstaller().install()

    assert calls == ["port_free", "install"]


def test_install_failed_preflight_stop_fails_loudly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A refused pre-flight stop is a clean SystemExit naming why, not a traceback."""
    _setup_fake_env(tmp_path, monkeypatch)
    _on(monkeypatch, "linux")
    _fake_backend(monkeypatch, SystemdBackend, installed=True)

    def refuse(self: SystemdBackend) -> None:
        raise PrivilegeDeniedError("sudo could not run `systemctl stop voxd`")

    monkeypatch.setattr(SystemdBackend, "request_stop", refuse)
    with pytest.raises(SystemExit, match="sudo could not run"):
        ServiceInstaller().install()


def test_install_broken_port_probe_fails_loudly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing fuser fails the install; it is never read as an empty port."""
    _setup_fake_env(tmp_path, monkeypatch)
    _on(monkeypatch, "linux")
    _fake_backend(monkeypatch, SystemdBackend)

    def broken(self: ProcessManager) -> None:
        raise PortProbeError("cannot run fuser: not found (install psmisc)")

    monkeypatch.setattr(ProcessManager, "ensure_port_free", broken)
    with pytest.raises(SystemExit, match="install psmisc"):
        ServiceInstaller().install()


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
# install() — health poll targets the exact installed daemon
# ---------------------------------------------------------------------------


def _write_run_token(fake_home: Path, token: str) -> None:
    """Write ``serve.token`` under the install's run dir so the poll reads it."""
    run_dir = fake_home / ".punt-labs" / "vox" / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "serve.token").write_text(token)


def test_install_health_poll_pins_installed_port_and_bind_over_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The poll targets DEFAULT_PORT and the unit's bind, never the shell's.

    A stray VOXD_PORT or VOXD_BIND in the install environment must not
    redirect the token-bearing poll to a different daemon than the one the
    service unit runs.
    """
    fake_home = _setup_fake_env(tmp_path, monkeypatch, bypass_health=False)
    _on(monkeypatch, "macos")
    _fake_backend(monkeypatch, LaunchdBackend, bind="192.168.1.50")
    _visible_voxd(monkeypatch)
    _write_run_token(fake_home, "run-token")
    monkeypatch.setenv("VOXD_PORT", "9999")
    monkeypatch.setenv("VOXD_BIND", "10.0.0.9")
    captured = _clients(monkeypatch, _OK)

    ServiceInstaller().install()

    assert captured, "VoxClientSync was never constructed"
    assert captured[0]["port"] == DEFAULT_PORT
    assert captured[0]["host"] == "192.168.1.50"


def test_install_health_poll_retries_transient_protocol_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A transient VoxdProtocolError is retried, not fatal.

    A receive timeout while voxd is still binding its port surfaces as
    VoxdProtocolError. The poll retries until the daemon answers rather than
    failing the install on the first hiccup.
    """
    fake_home = _setup_fake_env(tmp_path, monkeypatch, bypass_health=False)
    _on(monkeypatch, "macos")
    _fake_backend(monkeypatch, LaunchdBackend)
    _visible_voxd(monkeypatch)
    monkeypatch.setattr("punt_vox.service.health_verify._HEALTH_DEADLINE_S", 5.0)
    _write_run_token(fake_home, "run-token")
    captured = _clients(
        monkeypatch,
        [VoxdProtocolError("timeout waiting for response to 'health'"), _OK],
    )

    result = ServiceInstaller().install()

    assert len(captured) == 2
    assert "running" in result
    assert "not yet running" not in result


def test_install_health_poll_uses_run_file_token_not_shell_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The health client authenticates with serve.token, not a stray VOXD_TOKEN."""
    fake_home = _setup_fake_env(tmp_path, monkeypatch, bypass_health=False)
    _on(monkeypatch, "macos")
    _fake_backend(monkeypatch, LaunchdBackend)
    _visible_voxd(monkeypatch)
    monkeypatch.setenv("VOXD_TOKEN", "stray-shell-token")
    _write_run_token(fake_home, "authoritative-run-token")
    captured = _clients(monkeypatch, _OK)

    ServiceInstaller().install()

    assert captured, "VoxClientSync was never constructed"
    assert captured[0]["token"] == "authoritative-run-token"


def test_install_health_poll_without_run_file_builds_no_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no serve.token, no client is built, so VOXD_TOKEN cannot leak.

    voxd writes serve.token on startup; until it does the poll keeps waiting
    rather than sending a request that would fall back to the shell's token.
    """
    _setup_fake_env(tmp_path, monkeypatch, bypass_health=False)
    _on(monkeypatch, "macos")
    _fake_backend(monkeypatch, LaunchdBackend)
    _visible_voxd(monkeypatch)
    monkeypatch.setenv("VOXD_TOKEN", "stray-shell-token")
    captured = _clients(monkeypatch, _OK)

    with pytest.raises(SystemExit, match="never became reachable"):
        ServiceInstaller().install()

    assert captured == []


def test_install_health_poll_sends_no_token_without_a_visible_voxd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A listener vox cannot see gets no token-bearing request at all."""
    fake_home = _setup_fake_env(tmp_path, monkeypatch, bypass_health=False)
    _on(monkeypatch, "macos")
    _fake_backend(monkeypatch, LaunchdBackend)
    monkeypatch.setattr(DaemonProbe, "voxd_processes", lambda self: frozenset())
    monkeypatch.setattr("punt_vox.service.health_verify._HEALTH_DEADLINE_S", 0.05)
    _write_run_token(fake_home, "run-token")
    captured = _clients(monkeypatch, _OK)

    with pytest.raises(SystemExit, match="never became reachable"):
        ServiceInstaller().install()

    assert captured == []


# ---------------------------------------------------------------------------
# uninstall — surface a surviving daemon
# ---------------------------------------------------------------------------


@pytest.fixture()
def no_unit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(LaunchdBackend, "uninstall", lambda self: None)
    monkeypatch.setattr(SystemdBackend, "uninstall", lambda self: None)


@pytest.mark.parametrize("plat", ["macos", "linux"])
def test_uninstall_clean_when_daemon_killed(
    plat: str, monkeypatch: pytest.MonkeyPatch, no_unit: None
) -> None:
    """A confirmed kill reports success and skips the survival re-scan."""
    _on(monkeypatch, plat)
    with (
        patch.object(ProcessManager, "kill_stale_daemon", return_value=True),
        patch.object(ProcessManager, "find_pid_on_port") as mock_scan,
    ):
        msg = ServiceInstaller().uninstall()

    mock_scan.assert_not_called()
    assert "uninstalled" in msg


def test_uninstall_clean_when_nothing_to_kill(
    monkeypatch: pytest.MonkeyPatch, no_unit: None
) -> None:
    """A False kill result with an empty port is a clean uninstall, not a failure."""
    _on(monkeypatch, "macos")
    with (
        patch.object(ProcessManager, "kill_stale_daemon", return_value=False),
        patch.object(ProcessManager, "read_port_file", return_value=None),
        patch.object(ProcessManager, "find_pid_on_port", return_value=[]),
    ):
        msg = ServiceInstaller().uninstall()

    assert "uninstalled" in msg


@pytest.mark.parametrize("plat", ["macos", "linux"])
def test_uninstall_raises_when_daemon_survives(
    plat: str, monkeypatch: pytest.MonkeyPatch, no_unit: None
) -> None:
    """A surviving voxd daemon must surface as a failure, not plain success."""
    _on(monkeypatch, plat)
    with (
        patch.object(ProcessManager, "kill_stale_daemon", return_value=False),
        patch.object(ProcessManager, "read_port_file", return_value=DEFAULT_PORT),
        patch.object(ProcessManager, "find_pid_on_port", return_value=[4321]),
        patch.object(ProcessManager, "is_vox_daemon_process", return_value=True),
        pytest.raises(SystemExit) as excinfo,
    ):
        ServiceInstaller().uninstall()

    assert "still running" in str(excinfo.value)


def test_uninstall_clean_when_survivor_is_not_voxd(
    monkeypatch: pytest.MonkeyPatch, no_unit: None
) -> None:
    """A non-voxd process on the port is not our survivor — uninstall succeeds."""
    _on(monkeypatch, "macos")
    with (
        patch.object(ProcessManager, "kill_stale_daemon", return_value=False),
        patch.object(ProcessManager, "read_port_file", return_value=DEFAULT_PORT),
        patch.object(ProcessManager, "find_pid_on_port", return_value=[4321]),
        patch.object(ProcessManager, "is_vox_daemon_process", return_value=False),
    ):
        msg = ServiceInstaller().uninstall()

    assert "uninstalled" in msg


def test_uninstall_raises_when_survivor_not_first_pid(
    monkeypatch: pytest.MonkeyPatch, no_unit: None
) -> None:
    """A voxd survivor behind a client PID must still be found (any(), not [0])."""
    _on(monkeypatch, "macos")
    with (
        patch.object(ProcessManager, "kill_stale_daemon", return_value=False),
        patch.object(ProcessManager, "read_port_file", return_value=DEFAULT_PORT),
        patch.object(ProcessManager, "find_pid_on_port", return_value=[1111, 4321]),
        patch.object(
            ProcessManager, "is_vox_daemon_process", side_effect=[False, True]
        ) as mock_is_vox,
        pytest.raises(SystemExit) as excinfo,
    ):
        ServiceInstaller().uninstall()

    assert "still running" in str(excinfo.value)
    assert mock_is_vox.call_count == 2


def test_uninstall_broken_port_probe_fails_loudly(
    monkeypatch: pytest.MonkeyPatch, no_unit: None
) -> None:
    """A broken probe never reads as "nothing survived"."""
    _on(monkeypatch, "linux")

    def broken(self: ProcessManager) -> bool:
        raise PortProbeError("cannot run fuser: not found (install psmisc)")

    monkeypatch.setattr(ProcessManager, "kill_stale_daemon", broken)
    with pytest.raises(SystemExit, match="install psmisc"):
        ServiceInstaller().uninstall()


# ---------------------------------------------------------------------------
# backend_for — the one platform dispatch
# ---------------------------------------------------------------------------


def test_backend_for_picks_the_platform_backend() -> None:
    inst = ServiceInstaller()
    assert isinstance(inst.backend_for("macos"), LaunchdBackend)
    assert isinstance(inst.backend_for("linux"), SystemdBackend)
