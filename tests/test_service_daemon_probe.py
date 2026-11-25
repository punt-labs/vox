"""Tests for punt_vox.service.daemon_probe -- what is on the port, who answers."""

from __future__ import annotations

import ipaddress
import socket
import time
from unittest.mock import MagicMock

import pytest
from _lifecycle_fakes import FakeClock, holding_voxd, probe_over

from punt_vox.client_errors import VoxdConnectionError, VoxdProtocolError
from punt_vox.service.backend import PortProbeError
from punt_vox.service.clock import Poll
from punt_vox.service.daemon_probe import DaemonProbe
from punt_vox.service.deadline import Deadline
from punt_vox.service.health import HealthTarget
from punt_vox.service.health_request import HealthRequest
from punt_vox.service.port_file_cleanup import PortFileCleanup
from punt_vox.types_health import HealthStatus


def test_voxd_processes_keeps_only_voxd_handles() -> None:
    mgr = MagicMock()
    mgr.find_pid_on_port.return_value = [10, 20]
    voxd = MagicMock()

    def open_voxd(pid: int) -> MagicMock | None:
        return voxd if pid == 20 else None

    mgr.open_voxd.side_effect = open_voxd
    assert probe_over(mgr).voxd_processes() == frozenset({voxd})
    mgr.find_pid_on_port.assert_called_once_with(8421)


def test_broken_port_probe_propagates() -> None:
    mgr = MagicMock()
    mgr.find_pid_on_port.side_effect = PortProbeError("cannot run fuser")
    with pytest.raises(PortProbeError):
        probe_over(mgr).voxd_processes()


def test_off_port_voxd_named_from_serve_port() -> None:
    mgr = MagicMock()
    mgr.read_port_file.return_value = 9000
    mgr.find_pid_on_port.return_value = [900, 901]

    def is_voxd(pid: int) -> bool:
        return pid == 900

    mgr.is_vox_daemon_process.side_effect = is_voxd
    assert probe_over(mgr).off_port_voxd() == frozenset({900})
    mgr.find_pid_on_port.assert_called_once_with(9000)


@pytest.mark.parametrize("port", [None, 8421])
def test_off_port_voxd_empty_without_another_port(port: int | None) -> None:
    mgr = MagicMock()
    mgr.read_port_file.return_value = port
    assert probe_over(mgr).off_port_voxd() == frozenset()
    mgr.find_pid_on_port.assert_not_called()


def test_health_answers_through_the_target() -> None:
    target = MagicMock()
    status = HealthStatus(status="ok", pid=42)
    target.client.return_value.health.return_value = status
    mgr, held = holding_voxd()
    assert probe_over(mgr, target).health(frozenset({held}), 5.0) is status


def test_port_listening_sees_a_bare_listener() -> None:
    """A plain TCP connect, no token: sees listeners fuser cannot attribute."""
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        port = server.getsockname()[1]
        target = MagicMock(host="127.0.0.1", port=port)
        assert probe_over(MagicMock(), target).port_listening() is True
    target = MagicMock(host="127.0.0.1", port=port)
    assert probe_over(MagicMock(), target).port_listening() is False


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
    """Only a refused connection means "nothing listens"."""

    def connect(address: object, timeout: float) -> object:
        raise failure

    monkeypatch.setattr(
        "punt_vox.service.daemon_probe.socket.create_connection", connect
    )
    with pytest.raises(PortProbeError, match="cannot connect"):
        probe_over(MagicMock()).port_listening()


def test_health_miss_keeps_the_last_cause() -> None:
    """The probe remembers why the last gated request got no answer."""
    target = MagicMock()
    rejected = VoxdConnectionError("auth rejected (4401)")
    target.client.return_value.health.side_effect = rejected
    mgr, held = holding_voxd()
    probe = probe_over(mgr, target)
    assert probe.health_miss() is None
    assert probe.health(frozenset({held}), 5.0) is None
    assert probe.health_miss() is rejected


def test_clear_port_file_discards_a_stale_service_port_file() -> None:
    """With no visible voxd on 8421, serve.port naming it is discarded."""
    mgr = MagicMock()
    mgr.find_pid_on_port.return_value = []
    mgr.discard_port_file.return_value = PortFileCleanup.REMOVED
    assert probe_over(mgr).clear_port_file() is PortFileCleanup.REMOVED
    mgr.discard_port_file.assert_called_once_with(8421)


def test_clear_port_file_leaves_the_file_of_a_voxd_on_the_port() -> None:
    """A visible voxd still on 8421 may own serve.port: it is not removed."""
    mgr = MagicMock()
    mgr.find_pid_on_port.return_value = [20]
    mgr.open_voxd.return_value = MagicMock()
    assert probe_over(mgr).clear_port_file() is PortFileCleanup.HELD
    mgr.discard_port_file.assert_not_called()


def test_open_handle_pins_through_the_process_manager() -> None:
    """A handle on a pid that is gone, or not voxd, is None; a voxd is pinned."""
    mgr = MagicMock()
    pinned = MagicMock(pid=42)

    def open_voxd(pid: int) -> MagicMock | None:
        return pinned if pid == 42 else None

    mgr.open_voxd.side_effect = open_voxd
    probe = probe_over(mgr)
    assert probe.open_handle(42) is pinned
    assert probe.open_handle(43) is None


def test_bind_is_read_lazily_inside_the_verb() -> None:
    """An unreadable unit fails when the probe is used, not when built."""

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
    assert probe_over(mgr, target).wait_up(5.0) is status


def test_wait_up_never_outlives_its_deadline_on_a_hung_health_check() -> None:
    """The install wait: a health request that hangs 5 s cannot carry it past 5 s.

    Each attempt is handed the time that remains as its own bound, and no
    attempt runs once none remains.
    """
    clock = FakeClock()
    mgr = MagicMock()
    mgr.find_pid_on_port.return_value = [42]
    mgr.open_voxd.return_value = MagicMock()
    budgets: list[float | None] = []

    def hung(timeout: float | None = None) -> HealthStatus:
        budgets.append(timeout)
        clock.advance(5.0 if timeout is None else min(5.0, timeout))
        msg = "Timeout waiting for response to 'health'"
        raise VoxdProtocolError(msg)

    target = MagicMock()
    target.client.return_value.health.side_effect = hung
    probe = DaemonProbe(mgr, lambda: target, Poll(clock))
    assert probe.wait_up(5.0) is None
    assert clock.monotonic() <= 5.0 + Poll.INTERVAL_S
    assert budgets
    assert all(b is not None and b > 0 for b in budgets)
    assert isinstance(probe.health_miss(), VoxdProtocolError)


def test_wait_up_keeps_the_last_real_miss_below_the_floor() -> None:
    """The sliver after the last interval is not spent on a doomed request."""
    clock = FakeClock()
    mgr = MagicMock()
    mgr.find_pid_on_port.return_value = [42]
    mgr.open_voxd.return_value = MagicMock()
    target = MagicMock()
    rejected = VoxdConnectionError("auth rejected (4401)")
    target.client.return_value.health.side_effect = rejected
    probe = DaemonProbe(mgr, lambda: target, Poll(clock))
    assert probe.wait_up(1.0) is None
    assert probe.health_miss() is rejected
    budgets = [
        c.kwargs["timeout"] for c in target.client.return_value.health.mock_calls
    ]
    assert min(budgets) >= HealthRequest.MIN_S


def test_within_bounds_the_process_probes_by_the_deadline() -> None:
    mgr = MagicMock()
    deadline = Deadline.never()
    bounded = probe_over(mgr).within(deadline)
    mgr.within.assert_called_once_with(deadline)
    mgr.within.return_value.find_pid_on_port.return_value = []
    assert bounded.voxd_processes() == frozenset()


def test_port_listening_connect_is_cut_to_the_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [0.0]
    asked: list[float] = []

    def connect(address: object, timeout: float) -> object:
        asked.append(timeout)
        raise ConnectionRefusedError

    monkeypatch.setattr(
        "punt_vox.service.daemon_probe.socket.create_connection", connect
    )
    probe = probe_over(MagicMock()).within(Deadline(lambda: clock[0], 5.0))
    clock[0] = 4.9
    assert probe.port_listening() is False
    assert asked == [pytest.approx(0.1)]


def test_port_listening_after_the_deadline_never_connects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [0.0]
    connect = MagicMock()
    monkeypatch.setattr(
        "punt_vox.service.daemon_probe.socket.create_connection", connect
    )
    probe = probe_over(MagicMock()).within(Deadline(lambda: clock[0], 5.0))
    clock[0] = 6.0
    with pytest.raises(PortProbeError, match="deadline"):
        probe.port_listening()
    connect.assert_not_called()


# ---------------------------------------------------------------------------
# The listener check never waits on a name lookup
# ---------------------------------------------------------------------------


def _stalling_resolver(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Make every name lookup stall 6 s, as a hung DNS server would.

    An IP literal is parsed locally, as getaddrinfo does, with no wait.
    Returns the hosts that were looked up by name.
    """
    real = socket.getaddrinfo
    looked_up: list[str] = []

    def stalling(host: object, *args: object, **kwargs: object) -> object:
        try:
            ipaddress.ip_address(str(host))
        except ValueError:
            looked_up.append(str(host))
            time.sleep(6.0)
        return real(host, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(socket, "getaddrinfo", stalling)
    return looked_up


def test_a_stalling_resolver_cannot_push_status_past_its_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hostname bind is refused at once; nothing is ever looked up."""
    looked_up = _stalling_resolver(monkeypatch)
    probe = DaemonProbe(
        MagicMock(), lambda: HealthTarget("voxd.example"), Poll(FakeClock())
    )
    started = time.monotonic()
    with pytest.raises(PortProbeError, match="not an IP"):
        probe.within(Deadline(time.monotonic, 5.0)).port_listening()
    assert time.monotonic() - started < 5.0
    assert looked_up == []


def test_an_ip_bind_is_checked_without_any_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    looked_up = _stalling_resolver(monkeypatch)
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        target = MagicMock(host="127.0.0.1", port=server.getsockname()[1])
        assert probe_over(MagicMock(), target).port_listening() is True
    assert looked_up == []
