"""Row-by-row tests of ``vox daemon start`` (design section 4.2).

One test per outcome row, named for it, on fakes and a fake clock; each
asserts the model properties (P1, P4) its row can reach.
"""

from __future__ import annotations

from _lifecycle_fakes import (
    STALE,
    WHEEL,
    FakeClock,
    FakeWorld,
    assert_p1_one_outcome,
    assert_p4_token_only_to_visible_voxd,
    denied,
    failed,
    lifecycle,
)

from punt_vox.service.backend import BackendCommandError
from punt_vox.service.clock import Poll
from punt_vox.service.lifecycle_report import LifecycleOutcome as Outcome


def test_t1_not_installed() -> None:
    world = FakeWorld(installed=False)
    report = lifecycle(world).start()
    assert report.outcome is Outcome.NOT_INSTALLED
    assert "vox daemon install" in report.detail
    assert "request_start" not in world.calls
    assert_p1_one_outcome(report)


def test_t2_already_running() -> None:
    world = FakeWorld()
    world.run_unit(100)
    report = lifecycle(world).start()
    assert report.outcome is Outcome.ALREADY_RUNNING
    assert (report.pid, report.daemon_version, report.exit_code) == (100, WHEEL, 0)
    assert "request_start" not in world.calls
    assert_p4_token_only_to_visible_voxd(world, 100)
    assert_p1_one_outcome(report)


def test_t2b_stale_version_exit_0_standalone() -> None:
    world = FakeWorld()
    world.run_unit(100, version=STALE)
    report = lifecycle(world).start()
    assert report.outcome is Outcome.ALREADY_RUNNING
    assert report.exit_code == 0
    assert f"voxd runs version {STALE}, the wheel is {WHEEL}" in report.detail
    assert "vox daemon restart" in report.detail
    assert_p1_one_outcome(report)


def test_t2c_pid_not_mainpid_backendfailed() -> None:
    world = FakeWorld()
    world.run_unit(100, on_port=False)
    world.spawn(200)
    report = lifecycle(world).start()
    assert report.outcome is Outcome.BACKEND_FAILED
    assert "answered by pid 200, service manager runs pid 100" in report.detail
    assert_p4_token_only_to_visible_voxd(world, 200)
    assert_p1_one_outcome(report)


def test_t3_activating_then_serving() -> None:
    world = FakeWorld()
    main = world.run_unit(100, on_port=False)
    clock = FakeClock()
    clock.at(0.6, main.bind)
    report = lifecycle(world, clock).start()
    assert report.outcome is Outcome.ALREADY_RUNNING
    assert report.pid == 100
    assert_p4_token_only_to_visible_voxd(world, 100)
    assert_p1_one_outcome(report)


def test_t3b_no_token_file_no_connect() -> None:
    world = FakeWorld(token_written=False)
    world.run_unit(100)
    report = lifecycle(world).start()
    assert report.outcome is Outcome.TIMEOUT
    assert_p4_token_only_to_visible_voxd(world, 100)
    assert_p1_one_outcome(report)


def test_t4_port_conflict() -> None:
    world = FakeWorld()
    world.spawn(200)
    report = lifecycle(world).start()
    assert report.outcome is Outcome.PORT_CONFLICT
    assert "voxd pid 200, which the service manager does not run" in report.detail
    assert "vox daemon stop" in report.detail
    assert world.tokens_to == [], "the conflict is reported before any token"
    assert "request_start" not in world.calls
    assert_p1_one_outcome(report)


def test_t4b_invisible_holder_port_conflict_no_token_sent() -> None:
    world = FakeWorld(foreign_listener=True)
    report = lifecycle(world).start()
    assert report.outcome is Outcome.PORT_CONFLICT
    assert "a process vox cannot see" in report.detail
    assert world.tokens_to == []
    assert_p1_one_outcome(report)


def test_t5_permissiondenied() -> None:
    world = FakeWorld(start_takes_effect=False, start_error=denied())
    report = lifecycle(world).start()
    assert report.outcome is Outcome.PERMISSION_DENIED
    assert_p1_one_outcome(report)


def test_t6_backendfailed() -> None:
    world = FakeWorld(start_takes_effect=False, start_error=failed("start"))
    report = lifecycle(world).start()
    assert report.outcome is Outcome.BACKEND_FAILED
    assert "systemctl start voxd exited 1" in report.detail
    assert_p1_one_outcome(report)


def test_t7_timeout() -> None:
    """The unit starts but never serves (a crash loop on Restart=on-failure)."""
    world = FakeWorld(start_binds=False)
    report = lifecycle(world).start()
    assert report.outcome is Outcome.TIMEOUT
    assert "no health answer on port 8421 within 10s" in report.detail
    assert world.tokens_to == []
    assert "vox.log" in report.text()
    assert_p1_one_outcome(report)


def test_start_never_waits_past_its_deadline_on_a_hung_health_check() -> None:
    """Each health attempt blocks 5 s; start still returns by its 10 s wait.

    No attempt runs with no time left, and each is bounded by what remains,
    so the verb ends within the deadline plus at most one poll interval.
    """
    world = FakeWorld(health_blocks_s=5.0)
    world.run_unit(100)
    clock = FakeClock()
    report = lifecycle(world, clock).start()
    assert report.outcome is Outcome.TIMEOUT
    assert "no health answer on port 8421 within 10s" in report.detail
    assert "(last: connection refused)" in report.detail
    assert clock.monotonic() <= 10.0 + Poll.INTERVAL_S
    assert_p1_one_outcome(report)


def test_t8_foreign_answer_backendfailed() -> None:
    world = FakeWorld(start_binds=False)
    clock = FakeClock()

    def stray_binds() -> None:
        world.spawn(200)

    clock.at(0.4, stray_binds)
    report = lifecycle(world, clock).start()
    assert report.outcome is Outcome.BACKEND_FAILED
    assert "answered by pid 200, service manager runs pid 500" in report.detail
    assert_p1_one_outcome(report)


def test_t9_version_mismatch() -> None:
    world = FakeWorld(start_version=STALE)
    report = lifecycle(world).start()
    assert report.outcome is Outcome.VERSION_MISMATCH
    assert report.exit_code == 1
    assert_p1_one_outcome(report)


def test_t10_started() -> None:
    world = FakeWorld()
    report = lifecycle(world).start()
    assert report.outcome is Outcome.STARTED
    assert (report.pid, report.port, report.daemon_version) == (500, 8421, WHEEL)
    assert_p4_token_only_to_visible_voxd(world, 500)
    assert_p1_one_outcome(report)


def test_start_serving_without_main_pid_is_backendfailed() -> None:
    """The manager is re-activating (no main pid) and a stray voxd answers."""
    world = FakeWorld()
    world.unit_active = True
    world.spawn(200)
    report = lifecycle(world).start()
    assert report.outcome is Outcome.BACKEND_FAILED
    assert "answered by pid 200, service manager runs no process" in report.detail
    assert_p1_one_outcome(report)


def test_start_permission_denied_but_unit_came_up_is_started() -> None:
    """A refused start while a third party started the unit."""
    world = FakeWorld(start_error=denied())
    report = lifecycle(world).start()
    assert report.outcome is Outcome.STARTED
    assert_p1_one_outcome(report)


def test_start_query_failure_is_backend_failed_not_a_traceback() -> None:
    """A failing manager query is a report, never an escaping exception."""
    world = FakeWorld(query_error=failed("show"))
    report = lifecycle(world).start()
    assert report.outcome is Outcome.BACKEND_FAILED
    assert "systemctl show voxd exited 1" in report.detail
    assert_p1_one_outcome(report)


def test_start_query_failure_after_request_names_the_request() -> None:
    world = FakeWorld()

    def start_then_break() -> None:
        world.query_error = failed("show")

    world.start_error = None
    world.on_start = start_then_break
    report = lifecycle(world).start()
    assert report.outcome is Outcome.BACKEND_FAILED
    assert "after requesting start" in report.detail


def test_start_probe_error_type_reaches_the_report() -> None:
    world = FakeWorld(probe_error="cannot inspect pid 5: ps exited 2")
    report = lifecycle(world).start()
    assert report.outcome is Outcome.BACKEND_FAILED
    assert "ps exited 2" in report.detail


def test_start_timeout_names_the_health_cause() -> None:
    """The last health failure's cause is in the timeout detail."""
    world = FakeWorld(token_written=False, health_error="auth rejected (4401)")
    world.run_unit(100)
    report = lifecycle(world).start()
    assert report.outcome is Outcome.TIMEOUT
    assert "auth rejected (4401)" in report.detail


def test_start_overruled_refusal_is_carried_into_the_detail() -> None:
    """A refusal contradicted by the re-observation is still reported."""
    world = FakeWorld(start_error=denied())
    report = lifecycle(world).start()
    assert report.outcome is Outcome.STARTED
    assert "password is required" in report.detail


def test_start_backend_command_error_in_request_is_backend_failed() -> None:
    world = FakeWorld(
        start_takes_effect=False,
        start_error=BackendCommandError.of(["launchctl", "bootstrap"], 1, "x"),
    )
    report = lifecycle(world).start()
    assert report.outcome is Outcome.BACKEND_FAILED


def test_start_sends_no_token_to_a_voxd_that_left_before_it_was_asked() -> None:
    """The holder seen at attribution exits before the request: nothing sent."""
    world = FakeWorld(start_takes_effect=False)
    proc = world.run_unit(100)
    world.before_token = proc.exit
    lifecycle(world).start()
    assert frozenset({100}) not in world.tokens_to
