"""Tests for punt_vox.service.health_request -- one bounded, token-bearing request."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from _lifecycle_fakes import FakeClock, holding_voxd, probe_over

from punt_vox.client_errors import VoxdConnectionError, VoxdProtocolError
from punt_vox.service.deadline import Deadline
from punt_vox.service.health import DaemonNotUpError
from punt_vox.service.health_request import HealthRequest
from punt_vox.types_health import HealthStatus


def _request(target: MagicMock, within_s: float = 2.0) -> HealthRequest:
    return HealthRequest(target, Deadline(FakeClock().monotonic, within_s))


def test_send_returns_the_answer_and_keeps_no_miss() -> None:
    target = MagicMock()
    status = HealthStatus(status="ok", pid=42)
    target.client.return_value.health.return_value = status
    request = _request(target)
    assert request.send() is status
    assert request.miss is None


def test_send_hands_the_request_what_is_left_of_the_bound() -> None:
    clock = FakeClock()
    target = MagicMock()
    target.client.return_value.health.return_value = HealthStatus(status="ok")
    request = HealthRequest(target, Deadline(clock.monotonic, 2.0))
    clock.advance(0.75)
    request.send()
    target.client.return_value.health.assert_called_once_with(timeout=1.25)


@pytest.mark.parametrize(
    "failure",
    [
        DaemonNotUpError("voxd has not written serve.token yet"),
        VoxdConnectionError("auth rejected (4401)"),
        VoxdProtocolError("Timeout waiting for response to 'health'"),
        OSError("connection reset by peer"),
    ],
)
def test_each_failure_is_none_with_the_cause_kept(failure: Exception) -> None:
    target = MagicMock()
    target.client.return_value.health.side_effect = failure
    request = _request(target)
    assert request.send() is None
    assert request.miss is failure


def test_a_missing_token_sends_nothing() -> None:
    """``DaemonNotUpError`` comes from the client builder, before any connect."""
    target = MagicMock()
    target.client.side_effect = DaemonNotUpError("no token")
    request = _request(target)
    assert request.send() is None
    assert isinstance(request.miss, DaemonNotUpError)


def test_the_floor_is_half_a_second() -> None:
    """Below this a timeout measures vox's own probing, not voxd."""
    assert HealthRequest.MIN_S == 0.5


def test_health_bounds_the_request_by_the_budget_it_is_handed() -> None:
    target = MagicMock()
    target.client.return_value.health.return_value = HealthStatus(status="ok")
    mgr, held = holding_voxd()
    probe_over(mgr, target).health(frozenset({held}), 1.25)
    target.client.return_value.health.assert_called_once_with(timeout=1.25)


@pytest.mark.parametrize(
    "failure", [DaemonNotUpError("no token"), VoxdConnectionError("refused")]
)
def test_health_no_answer_is_none(failure: Exception) -> None:
    target = MagicMock()
    target.client.side_effect = failure
    mgr, held = holding_voxd()
    assert probe_over(mgr, target).health(frozenset({held}), 5.0) is None
