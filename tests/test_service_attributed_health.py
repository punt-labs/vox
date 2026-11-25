"""Tests for punt_vox.service.attributed_health -- the attributed door for the token.

Driven through ``DaemonProbe.health``, which composes the request: the gate
is what the probe's callers see.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from _lifecycle_fakes import FakeClock, holding_voxd, probe_over

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
    probe, target, held = _slow_scan(0.75)
    probe.health(frozenset({held}), 1.25)
    target.client.return_value.health.assert_called_once_with(timeout=0.5)


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
