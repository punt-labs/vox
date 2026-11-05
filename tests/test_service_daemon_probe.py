"""Tests for punt_vox.service.daemon_probe -- what is on the port, who answers."""

from __future__ import annotations

import ipaddress
import socket
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from _lifecycle_fakes import FakeClock

from punt_vox.client_errors import VoxdConnectionError, VoxdProtocolError
from punt_vox.service.backend import PortProbeError
from punt_vox.service.clock import Poll
from punt_vox.service.daemon_probe import DaemonProbe
from punt_vox.service.deadline import Deadline
from punt_vox.service.health import (
    DaemonNotUpError,
    HealthTarget,
    NotAskedError,
)
from punt_vox.service.port_file_cleanup import PortFileCleanup
from punt_vox.types_health import HealthStatus


def _probe(
    mgr: MagicMock, target: HealthTarget | MagicMock | None = None
) -> DaemonProbe:
    pinned = target or HealthTarget("")
    return DaemonProbe(mgr, lambda: pinned, Poll(FakeClock()))


def _holding() -> tuple[MagicMock, MagicMock]:
    """Return a process manager whose port 8421 holder is one voxd handle."""
    mgr = MagicMock()
    held = MagicMock(pid=42)
    mgr.find_pid_on_port.return_value = [42]
    mgr.open_voxd.return_value = held
    return mgr, held


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
    assert _probe(MagicMock(), target).health(frozenset(), 5.0) is None
    target.client.assert_not_called()


def test_health_answers_through_the_target() -> None:
    target = MagicMock()
    status = HealthStatus(status="ok", pid=42)
    target.client.return_value.health.return_value = status
    mgr, held = _holding()
    assert _probe(mgr, target).health(frozenset({held}), 5.0) is status


def test_health_bounds_the_request_by_the_budget_it_is_handed() -> None:
    target = MagicMock()
    target.client.return_value.health.return_value = HealthStatus(status="ok")
    mgr, held = _holding()
    _probe(mgr, target).health(frozenset({held}), 1.25)
    target.client.return_value.health.assert_called_once_with(timeout=1.25)


@pytest.mark.parametrize(
    "failure", [DaemonNotUpError("no token"), VoxdConnectionError("refused")]
)
def test_health_no_answer_is_none(failure: Exception) -> None:
    target = MagicMock()
    target.client.side_effect = failure
    mgr, held = _holding()
    assert _probe(mgr, target).health(frozenset({held}), 5.0) is None


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
        _probe(MagicMock()).port_listening()


def test_health_miss_keeps_the_last_cause() -> None:
    """The probe remembers why the last gated request got no answer."""
    target = MagicMock()
    rejected = VoxdConnectionError("auth rejected (4401)")
    target.client.return_value.health.side_effect = rejected
    mgr, held = _holding()
    probe = _probe(mgr, target)
    assert probe.health_miss() is None
    assert probe.health(frozenset({held}), 5.0) is None
    assert probe.health_miss() is rejected


def test_health_miss_names_the_closed_gate() -> None:
    probe = _probe(MagicMock())
    assert probe.health(frozenset(), 5.0) is None
    assert "no visible voxd" in str(probe.health_miss())


def test_service_owned_reads_the_cgroup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pid in voxd.service's cgroup is the manager's, not unmanaged."""
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
    """Only the system unit vox installs counts, not a user-scope voxd."""
    monkeypatch.setattr(DaemonProbe, "_PROC", tmp_path)
    (tmp_path / "42").mkdir()
    (tmp_path / "42" / "cgroup").write_text(line + "\n")
    assert _probe(MagicMock()).service_owned(42) is owned


def test_clear_port_file_discards_a_stale_service_port_file() -> None:
    """With no visible voxd on 8421, serve.port naming it is discarded."""
    mgr = MagicMock()
    mgr.find_pid_on_port.return_value = []
    mgr.discard_port_file.return_value = PortFileCleanup.REMOVED
    assert _probe(mgr).clear_port_file() is PortFileCleanup.REMOVED
    mgr.discard_port_file.assert_called_once_with(8421)


def test_clear_port_file_leaves_the_file_of_a_voxd_on_the_port() -> None:
    """A visible voxd still on 8421 may own serve.port: it is not removed."""
    mgr = MagicMock()
    mgr.find_pid_on_port.return_value = [20]
    mgr.open_voxd.return_value = MagicMock()
    assert _probe(mgr).clear_port_file() is PortFileCleanup.HELD
    mgr.discard_port_file.assert_not_called()


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
    assert _probe(mgr, target).wait_up(5.0) is status


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


def test_within_bounds_the_process_probes_by_the_deadline() -> None:
    mgr = MagicMock()
    deadline = Deadline.never()
    bounded = _probe(mgr).within(deadline)
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
    probe = _probe(MagicMock()).within(Deadline(lambda: clock[0], 5.0))
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
    probe = _probe(MagicMock()).within(Deadline(lambda: clock[0], 5.0))
    clock[0] = 6.0
    with pytest.raises(PortProbeError, match="deadline"):
        probe.port_listening()
    connect.assert_not_called()


# ---------------------------------------------------------------------------
# The holder is attributed again immediately before the token goes out
# ---------------------------------------------------------------------------


def test_health_sends_no_token_when_the_attributed_voxd_left_the_port() -> None:
    mgr, held = _holding()
    mgr.find_pid_on_port.return_value = []
    target = MagicMock()
    probe = _probe(mgr, target)
    assert probe.health(frozenset({held}), 5.0) is None
    target.client.assert_not_called()
    miss = probe.health_miss()
    assert isinstance(miss, NotAskedError)
    assert "voxd pid 42" in str(miss)
    assert "no token sent" in str(miss)


def test_health_sends_no_token_when_another_process_took_the_port() -> None:
    """Same pid number, different process: the pinned handles differ."""
    mgr, held = _holding()
    mgr.open_voxd.return_value = MagicMock(pid=42)
    target = MagicMock()
    assert _probe(mgr, target).health(frozenset({held}), 5.0) is None
    target.client.assert_not_called()


def _slow_scan(scan_s: float) -> tuple[DaemonProbe, MagicMock, MagicMock]:
    """A probe whose re-attribution scan (``fuser``/``ps``) takes *scan_s*."""
    clock = FakeClock()
    mgr, held = _holding()

    def slow_scan(port: int) -> list[int]:
        clock.advance(scan_s)
        return [42] if port == 8421 else []

    mgr.find_pid_on_port.side_effect = slow_scan
    target = MagicMock()
    target.client.return_value.health.return_value = HealthStatus(status="ok")
    return DaemonProbe(mgr, lambda: target, Poll(clock)), target, held


def test_health_is_handed_only_what_the_rescan_left_of_its_budget() -> None:
    probe, target, held = _slow_scan(0.75)
    probe.health(frozenset({held}), 1.25)
    target.client.return_value.health.assert_called_once_with(timeout=0.5)


def test_a_rescan_that_spends_the_budget_sends_no_token() -> None:
    probe, target, held = _slow_scan(2.0)
    assert probe.health(frozenset({held}), 1.25) is None
    target.client.assert_not_called()
    miss = probe.health_miss()
    assert isinstance(miss, NotAskedError)
    assert "no token sent" in str(miss)


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
        assert _probe(MagicMock(), target).port_listening() is True
    assert looked_up == []
