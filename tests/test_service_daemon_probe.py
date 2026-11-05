"""Tests for punt_vox.service.daemon_probe -- what is on the port, who answers."""

from __future__ import annotations

import socket
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from _lifecycle_fakes import FakeClock

from punt_vox.client_errors import VoxdConnectionError
from punt_vox.service.backend import PortProbeError
from punt_vox.service.clock import Poll
from punt_vox.service.daemon_probe import DaemonProbe
from punt_vox.service.health import DaemonNotUpError, HealthTarget
from punt_vox.types_health import HealthStatus


def _probe(
    mgr: MagicMock, target: HealthTarget | MagicMock | None = None
) -> DaemonProbe:
    pinned = target or HealthTarget("")
    return DaemonProbe(mgr, lambda: pinned, Poll(FakeClock()))


def test_voxd_processes_keeps_only_voxd_handles() -> None:
    mgr = MagicMock()
    mgr.find_pid_on_port.return_value = [10, 20]
    voxd = MagicMock()

    def open_voxd(pid: int) -> MagicMock | None:
        return voxd if pid == 20 else None

    mgr.open_voxd.side_effect = open_voxd
    assert _probe(mgr).voxd_processes() == frozenset({voxd})
    mgr.find_pid_on_port.assert_called_once_with(8421)


def test_broken_port_probe_propagates() -> None:
    mgr = MagicMock()
    mgr.find_pid_on_port.side_effect = PortProbeError("cannot run fuser")
    with pytest.raises(PortProbeError):
        _probe(mgr).voxd_processes()


def test_off_port_voxd_named_from_serve_port() -> None:
    mgr = MagicMock()
    mgr.read_port_file.return_value = 9000
    mgr.find_pid_on_port.return_value = [900, 901]

    def is_voxd(pid: int) -> bool:
        return pid == 900

    mgr.is_vox_daemon_process.side_effect = is_voxd
    assert _probe(mgr).off_port_voxd() == frozenset({900})
    mgr.find_pid_on_port.assert_called_once_with(9000)


@pytest.mark.parametrize("port", [None, 8421])
def test_off_port_voxd_empty_without_another_port(port: int | None) -> None:
    mgr = MagicMock()
    mgr.read_port_file.return_value = port
    assert _probe(mgr).off_port_voxd() == frozenset()
    mgr.find_pid_on_port.assert_not_called()


def test_health_sends_no_request_without_a_visible_voxd() -> None:
    """P4: no visible voxd holder, no token-bearing connection at all."""
    target = MagicMock()
    assert _probe(MagicMock(), target).health(frozenset()) is None
    target.client.assert_not_called()


def test_health_answers_through_the_target() -> None:
    target = MagicMock()
    status = HealthStatus(status="ok", pid=42)
    target.client.return_value.health.return_value = status
    assert _probe(MagicMock(), target).health(frozenset({MagicMock()})) is status


@pytest.mark.parametrize(
    "failure", [DaemonNotUpError("no token"), VoxdConnectionError("refused")]
)
def test_health_no_answer_is_none(failure: Exception) -> None:
    target = MagicMock()
    target.client.side_effect = failure
    assert _probe(MagicMock(), target).health(frozenset({MagicMock()})) is None


def test_port_listening_sees_a_bare_listener() -> None:
    """A plain TCP connect, no token: sees listeners fuser cannot attribute."""
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        port = server.getsockname()[1]
        target = MagicMock(host="127.0.0.1", port=port)
        assert _probe(MagicMock(), target).port_listening() is True
    target = MagicMock(host="127.0.0.1", port=port)
    assert _probe(MagicMock(), target).port_listening() is False


# -- round 2 ---------------------------------------------------------------


@pytest.mark.parametrize(
    "failure",
    [
        TimeoutError("timed out"),
        ConnectionResetError("reset"),
        OSError(113, "no route"),
    ],
)
def test_port_listening_fails_closed_except_on_refusal(
    failure: OSError, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B2: only a refused connection means "nothing listens"."""

    def connect(address: object, timeout: float) -> object:
        raise failure

    monkeypatch.setattr(
        "punt_vox.service.daemon_probe.socket.create_connection", connect
    )
    with pytest.raises(PortProbeError, match="cannot connect"):
        _probe(MagicMock()).port_listening()


def test_health_miss_keeps_the_last_cause() -> None:
    """B9: the probe remembers why the last gated request got no answer."""
    target = MagicMock()
    rejected = VoxdConnectionError("auth rejected (4401)")
    target.client.return_value.health.side_effect = rejected
    probe = _probe(MagicMock(), target)
    assert probe.health_miss() is None
    assert probe.health(frozenset({MagicMock()})) is None
    assert probe.health_miss() is rejected


def test_health_miss_names_the_closed_gate() -> None:
    probe = _probe(MagicMock())
    assert probe.health(frozenset()) is None
    assert "no visible voxd" in str(probe.health_miss())


def test_service_owned_reads_the_cgroup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A2: a pid in voxd.service's cgroup is the manager's, not unmanaged."""
    monkeypatch.setattr(DaemonProbe, "_PROC", tmp_path)
    (tmp_path / "42").mkdir()
    (tmp_path / "42" / "cgroup").write_text("0::/system.slice/voxd.service\n")
    (tmp_path / "43").mkdir()
    (tmp_path / "43" / "cgroup").write_text("0::/user.slice/user-1000.slice\n")
    probe = _probe(MagicMock())
    assert probe.service_owned(42) is True
    assert probe.service_owned(43) is False
    assert probe.service_owned(44) is False  # gone, or no /proc (macOS)


@pytest.mark.parametrize(
    ("line", "owned"),
    [
        ("0::/system.slice/voxd.service", True),
        ("0::/system.slice/voxd.service/sub", True),
        ("1:name=systemd:/system.slice/voxd.service", True),
        (
            "0::/user.slice/user-1000.slice/user@1000.service/app.slice/voxd.service",
            False,
        ),
        ("0::/system.slice/voxd.service-other", False),
    ],
)
def test_service_owned_matches_the_system_unit_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, line: str, owned: bool
) -> None:
    """R9: only the system unit vox installs counts, not a user-scope voxd."""
    monkeypatch.setattr(DaemonProbe, "_PROC", tmp_path)
    (tmp_path / "42").mkdir()
    (tmp_path / "42" / "cgroup").write_text(line + "\n")
    assert _probe(MagicMock()).service_owned(42) is owned


def test_clear_port_file_only_for_the_service_port() -> None:
    """B6: serve.port is removed only when it names 8421."""
    mgr = MagicMock()
    mgr.read_port_file.return_value = 8421
    _probe(mgr).clear_port_file()
    mgr.remove_port_file.assert_called_once_with()
    mgr = MagicMock()
    mgr.read_port_file.return_value = 9000
    _probe(mgr).clear_port_file()
    mgr.remove_port_file.assert_not_called()


def test_bind_is_read_lazily_inside_the_verb() -> None:
    """A3: an unreadable unit fails when the probe is used, not when built."""

    def unreadable() -> HealthTarget:
        raise PortProbeError("cannot read the unit")

    probe = DaemonProbe(MagicMock(), unreadable, Poll(FakeClock()))
    with pytest.raises(PortProbeError):
        probe.port_listening()


def test_wait_up_returns_the_first_gated_answer() -> None:
    mgr = MagicMock()
    mgr.find_pid_on_port.return_value = [42]
    mgr.open_voxd.return_value = MagicMock()
    target = MagicMock()
    status = HealthStatus(status="ok", pid=42)
    target.client.return_value.health.side_effect = [
        VoxdConnectionError("binding"),
        status,
    ]
    assert _probe(mgr, target).wait_up(5.0) is status
