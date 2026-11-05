"""``DaemonLifecycle.status``: what the probe sees decides the state.

Only an answer is ``running``; a visible voxd that does not answer is
``unresponsive``; a listener vox cannot attribute, or a probe it cannot run, is
``unknown``; only an empty, silent port is ``not_running``.
"""

from __future__ import annotations

from _lifecycle_fakes import (
    FakeClock,
    FakeWorld,
    assert_p4_token_only_to_visible_voxd,
    lifecycle,
)

from punt_vox.service.backend import PortProbeError
from punt_vox.service.daemon_status import DaemonState
from punt_vox.service.lifecycle import DaemonLifecycle
from punt_vox.service.lifecycle_status import StatusCheck


def test_status_a_voxd_that_answers_is_running() -> None:
    world = FakeWorld()
    world.run_unit(100)
    reading = lifecycle(world).status()
    assert reading.state is DaemonState.RUNNING
    assert reading.health is not None
    assert reading.health.pid == 100
    assert_p4_token_only_to_visible_voxd(world, 100)


def test_status_visible_voxd_no_reply_is_unresponsive() -> None:
    world = FakeWorld(token_written=False, health_error="auth rejected (4401)")
    world.run_unit(100)
    reading = lifecycle(world).status()
    assert reading.state is DaemonState.UNRESPONSIVE
    assert reading.pids == (100,)
    assert "auth rejected (4401)" in reading.reason


def test_status_a_hung_voxd_is_unresponsive_not_not_running() -> None:
    world = FakeWorld(health_blocks_s=60.0, health_error="no answer within 5.0s")
    world.run_unit(100)
    reading = lifecycle(world).status()
    assert reading.state is DaemonState.UNRESPONSIVE
    assert "no answer within 5.0s" in reading.reason


def test_status_unattributed_listener_is_unknown_and_gets_no_token() -> None:
    world = FakeWorld(foreign_listener=True)
    reading = lifecycle(world).status()
    assert reading.state is DaemonState.UNKNOWN
    assert "no visible voxd" in reading.reason
    assert world.tokens_to == []


def test_status_empty_silent_port_is_not_running() -> None:
    reading = lifecycle(FakeWorld()).status()
    assert reading.state is DaemonState.NOT_RUNNING
    assert reading.off_port == ()


def test_status_not_running_names_an_off_port_voxd() -> None:
    reading = lifecycle(FakeWorld(off_port=frozenset({777}))).status()
    assert reading.state is DaemonState.NOT_RUNNING
    assert reading.off_port == (777,)
    assert "voxd pid 777 on another port" in reading.reason


def test_status_a_listener_check_that_fails_is_unknown() -> None:
    world = FakeWorld(
        probe_failures={"port_listening": PortProbeError("connect timed out")}
    )
    reading = lifecycle(world).status()
    assert reading.state is DaemonState.UNKNOWN
    assert reading.reason == "connect timed out"


def test_status_slow_probes_cannot_push_status_past_its_deadline() -> None:
    """Port and process probes and the health request share one bound."""
    world = FakeWorld(probe_blocks_s=3.0, health_blocks_s=60.0)
    world.health_error = "no answer from voxd within the time left"
    world.run_unit(100)
    clock = FakeClock()
    reading = lifecycle(world, clock).status()
    assert clock.monotonic() <= DaemonLifecycle.STATUS_DEADLINE_S
    assert reading.state is DaemonState.UNRESPONSIVE


def test_status_deadline_spent_on_probes_is_unknown_and_sends_no_token() -> None:
    world = FakeWorld(probe_blocks_s=60.0)
    world.run_unit(100)
    clock = FakeClock()
    reading = lifecycle(world, clock).status()
    assert clock.monotonic() == DaemonLifecycle.STATUS_DEADLINE_S
    assert reading.state is DaemonState.UNKNOWN
    assert "timed out" in reading.reason
    assert world.tokens_to == []


def test_status_deadline_spent_probing_is_unknown_not_unresponsive() -> None:
    """A sliver left after vox's own probes is never blamed on voxd."""
    world = FakeWorld()
    world.run_unit(100)
    clock = FakeClock()
    world.before_holders = lambda: clock.advance(
        DaemonLifecycle.STATUS_DEADLINE_S - 0.01
    )
    reading = lifecycle(world, clock).status()
    assert reading.state is DaemonState.UNKNOWN
    assert "before voxd could be asked" in reading.reason
    assert world.tokens_to == []


def test_status_with_the_minimum_health_budget_left_still_asks_voxd() -> None:
    world = FakeWorld()
    world.run_unit(100)
    clock = FakeClock()
    world.before_holders = lambda: clock.advance(
        DaemonLifecycle.STATUS_DEADLINE_S - StatusCheck.MIN_HEALTH_S
    )
    reading = lifecycle(world, clock).status()
    assert reading.state is DaemonState.RUNNING
    assert world.tokens_to == [frozenset({100})]
