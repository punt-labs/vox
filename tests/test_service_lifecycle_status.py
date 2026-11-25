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
from punt_vox.service.health_request import HealthRequest
from punt_vox.service.lifecycle import DaemonLifecycle


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
    assert reading.reason.startswith("connect timed out; voxd was not asked first")


def test_status_slow_probes_cannot_push_status_past_its_deadline() -> None:
    """Port and process probes and the health request share one bound.

    Two 3 s probes do not fit in 5 s: the re-attribution before the token
    goes out is cut off, and the reading is ``unknown`` naming it, never
    ``unresponsive`` -- voxd was not asked.
    """
    world = FakeWorld(probe_blocks_s=3.0, health_blocks_s=60.0)
    world.health_error = "no answer from voxd within the time left"
    world.run_unit(100)
    clock = FakeClock()
    reading = lifecycle(world, clock).status()
    assert clock.monotonic() <= DaemonLifecycle.STATUS_DEADLINE_S
    assert reading.state is DaemonState.UNKNOWN
    assert reading.reason.startswith("cannot run fuser: timed out after 2s")
    assert world.tokens_to == []


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
        DaemonLifecycle.STATUS_DEADLINE_S - HealthRequest.MIN_S
    )
    reading = lifecycle(world, clock).status()
    assert reading.state is DaemonState.RUNNING
    assert world.tokens_to == [frozenset({100})]


def test_status_sends_no_token_to_a_voxd_that_left_before_it_was_asked() -> None:
    world = FakeWorld()
    proc = world.run_unit(100)
    world.before_token = proc.exit
    reading = lifecycle(world).status()
    assert world.tokens_to == []
    assert reading.state is DaemonState.UNKNOWN
    assert "left port 8421" in reading.reason


# ---------------------------------------------------------------------------
# voxd is asked first, through the port serve.port publishes, before
# lsof/fuser attribute the port. A slow lsof cannot hide a voxd that answers.
# ---------------------------------------------------------------------------


def test_status_asks_the_published_port_before_lsof_runs() -> None:
    """A voxd that answers health is running, even when lsof times out."""
    world = FakeWorld(
        port_file=8421, probe_error="cannot run lsof: timed out after 1.81 seconds"
    )
    world.run_unit(100)
    reading = lifecycle(world).status()
    assert reading.state is DaemonState.RUNNING
    assert reading.health is not None
    assert reading.health.pid == 100
    assert world.tokens_to_port == [8421]
    assert world.tokens_to == [], "no attributed request was needed"


def test_status_sends_no_published_token_without_serve_port() -> None:
    """With no serve.port, the fast path sends nothing; attribution decides."""
    world = FakeWorld()
    world.run_unit(100)
    reading = lifecycle(world).status()
    assert reading.state is DaemonState.RUNNING
    assert world.tokens_to_port == []
    assert world.tokens_to == [frozenset({100})]


def test_status_sends_no_published_token_when_serve_port_names_another_port() -> None:
    world = FakeWorld(port_file=9000)
    world.run_unit(100)
    reading = lifecycle(world).status()
    assert reading.state is DaemonState.RUNNING
    assert world.tokens_to_port == []


def test_status_sends_no_published_token_to_an_empty_port() -> None:
    """A stale serve.port naming 8421 with nothing listening sends no token."""
    world = FakeWorld(port_file=8421)
    reading = lifecycle(world).status()
    assert reading.state is DaemonState.NOT_RUNNING
    assert world.tokens_to_port == []


def test_status_published_port_no_answer_falls_back_to_attribution() -> None:
    """A rejected fast request is not the reading: lsof still names the holder."""
    world = FakeWorld(
        port_file=8421, token_written=False, health_error="auth rejected (4401)"
    )
    world.run_unit(100)
    reading = lifecycle(world).status()
    assert reading.state is DaemonState.UNRESPONSIVE
    assert reading.pids == (100,)
    assert world.tokens_to_port == [8421]


def test_status_hung_published_health_leaves_time_for_attribution() -> None:
    """A hung voxd spends only the reserve; attribution and the second ask fit."""
    world = FakeWorld(
        port_file=8421, health_blocks_s=60.0, health_error="no answer in time"
    )
    world.run_unit(100)
    clock = FakeClock()
    reading = lifecycle(world, clock).status()
    assert reading.state is DaemonState.UNRESPONSIVE
    assert clock.monotonic() <= DaemonLifecycle.STATUS_DEADLINE_S
    assert world.tokens_to_port == [8421]
    assert world.tokens_to == [frozenset({100})]


def test_status_published_miss_then_attribution_is_running() -> None:
    """A fast request that fails is not the reading: lsof finds the holder."""
    world = FakeWorld(port_file=8421, published_health_error="connection reset")
    world.run_unit(100)
    reading = lifecycle(world).status()
    assert reading.state is DaemonState.RUNNING
    assert world.tokens_to_port == [8421]
    assert world.tokens_to == [frozenset({100})]


def test_status_unknown_names_the_health_miss_and_the_probe_that_failed() -> None:
    """Both steps failed within the deadline: the reason names each one."""
    world = FakeWorld(
        port_file=8421,
        published_health_error="connection reset",
        probe_error="cannot run lsof: timed out after 0.5 seconds",
    )
    world.run_unit(100)
    reading = lifecycle(world).status()
    assert reading.state is DaemonState.UNKNOWN
    assert reading.reason == (
        "cannot run lsof: timed out after 0.5 seconds; "
        "voxd was asked first and gave no answer: connection reset"
    )


def test_status_unknown_names_why_voxd_was_not_asked_first() -> None:
    """With no serve.port the fast path sent nothing; the reason says so."""
    world = FakeWorld(probe_error="cannot run lsof: timed out after 2 seconds")
    world.run_unit(100)
    reading = lifecycle(world).status()
    assert reading.state is DaemonState.UNKNOWN
    assert reading.reason == (
        "cannot run lsof: timed out after 2 seconds; voxd was not asked first: "
        "serve.port publishes no port; no token sent unattributed"
    )


def test_status_a_slow_second_lsof_is_unknown_naming_it_never_unresponsive() -> None:
    """The re-attribution leaves less than a request's floor: no token, unknown."""
    world = FakeWorld(
        port_file=8421, published_health_error="connection reset", probe_blocks_s=2.3
    )
    world.run_unit(100)
    reading = lifecycle(world).status()
    assert reading.state is DaemonState.UNKNOWN
    assert "re-checking port 8421 spent the health budget" in reading.reason
    assert "voxd was asked first and gave no answer: connection reset" in reading.reason
    assert world.tokens_to == []


def test_status_deadline_spent_after_a_hung_first_ask_names_both() -> None:
    """A hung published request, then lsof spends the rest: the reason says so."""
    world = FakeWorld(
        port_file=8421,
        health_blocks_s=60.0,
        health_error="no answer in time",
        probe_blocks_s=3.2,
    )
    world.run_unit(100)
    reading = lifecycle(world).status()
    assert reading.state is DaemonState.UNKNOWN
    assert reading.reason.startswith("the status deadline was spent probing port 8421")
    assert (
        "voxd was asked first and gave no answer: no answer in time" in reading.reason
    )
    assert world.tokens_to == []


def test_status_unattributed_listener_names_the_first_ask() -> None:
    """A listener vox cannot attribute was asked through serve.port first."""
    world = FakeWorld(
        port_file=8421, foreign_listener=True, published_health_error="connection reset"
    )
    reading = lifecycle(world).status()
    assert reading.state is DaemonState.UNKNOWN
    assert reading.reason.startswith("something listens on port 8421")
    assert "voxd was asked first and gave no answer: connection reset" in reading.reason
    assert world.tokens_to == []
