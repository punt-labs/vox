"""Tests for punt_vox.service.attributed_health -- the attributed door for the token.

Driven through ``DaemonProbe.health``, which composes the request: the gate
is what the probe's callers see.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from _lifecycle_fakes import FakeClock, holding_voxd, probe_over

from punt_vox.service.backend import PortProbeError
from punt_vox.service.clock import Poll
from punt_vox.service.daemon_probe import DaemonProbe
from punt_vox.service.not_asked import NotAskedError
from punt_vox.types_health import HealthStatus


def test_health_sends_no_request_without_a_visible_voxd() -> None:
    """P4: no visible voxd holder, no token-bearing connection at all."""
    target = MagicMock()
    assert probe_over(MagicMock(), target).health(frozenset(), 5.0) is None
    target.client.assert_not_called()


def test_health_miss_names_the_closed_gate() -> None:
    probe = probe_over(MagicMock())
    assert probe.health(frozenset(), 5.0) is None
    assert "no visible voxd" in str(probe.health_miss())


def test_health_sends_no_token_when_the_attributed_voxd_left_the_port() -> None:
    mgr, held = holding_voxd()
    mgr.find_pid_on_port.return_value = []
    target = MagicMock()
    probe = probe_over(mgr, target)
    assert probe.health(frozenset({held}), 5.0) is None
    target.client.assert_not_called()
    miss = probe.health_miss()
    assert isinstance(miss, NotAskedError)
    assert "voxd pid 42" in str(miss)
    assert "no token sent" in str(miss)


def test_health_sends_no_token_when_another_process_took_the_port() -> None:
    """Same pid number, different process: the pinned handles differ."""
    mgr, held = holding_voxd()
    mgr.open_voxd.return_value = MagicMock(pid=42)
    target = MagicMock()
    assert probe_over(mgr, target).health(frozenset({held}), 5.0) is None
    target.client.assert_not_called()


def _slow_scan(scan_s: float) -> tuple[DaemonProbe, MagicMock, MagicMock]:
    """A probe whose re-attribution scan (``fuser``/``ps``) takes *scan_s*."""
    clock = FakeClock()
    mgr, held = holding_voxd()

    def slow_scan(port: int) -> list[int]:
        clock.advance(scan_s)
        return [42] if port == 8421 else []

    mgr.find_pid_on_port.side_effect = slow_scan
    target = MagicMock()
    target.client.return_value.health.return_value = HealthStatus(status="ok")
    return DaemonProbe(mgr, lambda: target, Poll(clock)), target, held


def test_health_is_handed_only_what_the_rescan_left_of_its_budget() -> None:
    """The rescan spends 0.5 of 1.25; the holder check gets a slice of what is
    left above the floor (0.25), and the request the rest (0.75)."""
    probe, target, held = _slow_scan(0.5)
    probe.health(frozenset({held}), 1.25)
    target.client.return_value.health.assert_called_once_with(timeout=0.75)


def test_a_rescan_that_leaves_only_the_floor_has_no_slice_for_the_holder_check() -> (
    None
):
    """Exactly the floor left: no holder check can be afforded, so no token."""
    probe, target, held = _slow_scan(0.75)
    assert probe.health(frozenset({held}), 1.25) is None
    target.client.assert_not_called()
    assert "no time left for the holder check" in str(probe.health_miss())


@pytest.mark.parametrize("scan_s", [2.0, 1.24])
def test_a_rescan_that_spends_the_budget_sends_no_token(scan_s: float) -> None:
    """Nothing left, or less than a request's floor: a 10 ms request would
    read a healthy voxd as unresponsive, so none is sent."""
    probe, target, held = _slow_scan(scan_s)
    assert probe.health(frozenset({held}), 1.25) is None
    target.client.assert_not_called()
    miss = probe.health_miss()
    assert isinstance(miss, NotAskedError)
    assert "no token sent" in str(miss)


def test_a_holder_without_the_listener_is_not_asked() -> None:
    """Linux ``fuser`` lists connected clients too: a draining voxd whose
    listener is closed, or a client, is a pid on the port but not its holder."""
    mgr, held = holding_voxd()
    mgr.within.return_value.holds_port.return_value = False
    target = MagicMock(host="127.0.0.1")
    probe = probe_over(mgr, target)
    assert probe.health(frozenset({held}), 5.0) is None
    target.client.assert_not_called()
    miss = probe.health_miss()
    assert isinstance(miss, NotAskedError)
    assert str(miss) == (
        "voxd pid 42 has a socket on port 8421 but holds no listener there; "
        "no token sent"
    )
    mgr.within.return_value.holds_port.assert_called_once_with(42, "127.0.0.1", 8421)


def test_the_holder_check_is_made_for_the_targets_address() -> None:
    """The unit polls ::1: a holder of 127.0.0.1:8421 is not the listener reached."""
    mgr, held = holding_voxd()
    mgr.within.return_value.holds_port.return_value = False
    target = MagicMock(host="::1", port=8421)
    probe = probe_over(mgr, target)
    assert probe.health(frozenset({held}), 5.0) is None
    target.client.assert_not_called()
    mgr.within.return_value.holds_port.assert_called_once_with(42, "::1", 8421)


def test_a_holder_check_that_cannot_be_made_is_not_asked() -> None:
    mgr, held = holding_voxd()
    mgr.within.return_value.holds_port.side_effect = PortProbeError(
        "cannot read /proc/42/fd: Permission denied"
    )
    target = MagicMock()
    probe = probe_over(mgr, target)
    assert probe.health(frozenset({held}), 5.0) is None
    target.client.assert_not_called()
    assert "cannot tell whether the publisher holds port 8421" in str(
        probe.health_miss()
    )


def test_a_holder_check_that_overruns_into_the_floor_sends_no_token() -> None:
    """The slice bounds lsof's timeout, but the last readlink or a subprocess
    finishing as its timeout fires can still overrun; a request with less
    than the floor left would read a healthy voxd as unresponsive."""
    clock = FakeClock()
    mgr, held = holding_voxd()

    def overrunning_check(pid: int, host: str, port: int) -> bool:
        clock.advance(1.0)  # 0.25 of the 1.25 bound left: under the floor
        return True

    mgr.within.return_value.holds_port.side_effect = overrunning_check
    target = MagicMock(host="127.0.0.1")
    probe = DaemonProbe(mgr, lambda: target, Poll(clock))
    assert probe.health(frozenset({held}), 1.25) is None
    target.client.assert_not_called()
    miss = probe.health_miss()
    assert isinstance(miss, NotAskedError)
    assert "spent the health budget" in str(miss)
