"""Tests for punt_vox.service.daemon_probe -- what is on the port, who answers."""

from __future__ import annotations

import socket
from unittest.mock import MagicMock

import pytest
from _lifecycle_fakes import FakeClock

from punt_vox.client_errors import VoxdConnectionError
from punt_vox.service.backend import PortProbeError
from punt_vox.service.clock import Poll
from punt_vox.service.daemon_probe import DaemonProbe
from punt_vox.service.health import DaemonNotUpError, HealthTarget
from punt_vox.types_health import HealthStatus


def _probe(mgr: MagicMock, target: HealthTarget | None = None) -> DaemonProbe:
    return DaemonProbe(mgr, target or HealthTarget(""), Poll(FakeClock()))


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
