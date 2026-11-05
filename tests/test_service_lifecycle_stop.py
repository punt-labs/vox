"""Row-by-row tests of ``vox daemon stop`` (design section 4.1).

One test per outcome row, named for it, on fakes and a fake clock; each
asserts the model properties (P1, P5) its row can reach.
"""

from __future__ import annotations

from _lifecycle_fakes import (
    FakeClock,
    FakeWorld,
    assert_p1_one_outcome,
    assert_p5_no_signal,
    denied,
    failed,
    lifecycle,
)

from punt_vox.service.lifecycle_report import LifecycleOutcome as Outcome


def test_s1_not_installed() -> None:
    world = FakeWorld(installed=False)
    world.spawn(200)
    report = lifecycle(world).stop()
    assert report.outcome is Outcome.NOT_INSTALLED
    assert report.exit_code == 1
    assert "voxd pid 200 on port 8421 was not touched" in report.detail
    assert "request_stop" not in world.calls
    assert_p5_no_signal(world, 200)
    assert_p1_one_outcome(report)


def test_s2_already_stopped_makes_no_sudo_call() -> None:
    world = FakeWorld()
    report = lifecycle(world).stop()
    assert report.outcome is Outcome.ALREADY_STOPPED
    assert report.exit_code == 0
    assert "request_stop" not in world.calls
    assert_p1_one_outcome(report)


def test_s3_permissiondenied() -> None:
    world = FakeWorld(stop_takes_effect=False, stop_error=denied())
    world.run_unit(100)
    report = lifecycle(world).stop()
    assert report.outcome is Outcome.PERMISSION_DENIED
    assert "password is required" in report.detail
    assert report.previous_pid == 100
    assert_p5_no_signal(world, 100)
    assert_p1_one_outcome(report)


def test_s4_backendfailed() -> None:
    world = FakeWorld(stop_takes_effect=False, stop_error=failed("stop"))
    world.run_unit(100)
    report = lifecycle(world).stop()
    assert report.outcome is Outcome.BACKEND_FAILED
    assert "systemctl stop voxd exited 1: Job failed" in report.detail
    assert_p5_no_signal(world, 100)
    assert_p1_one_outcome(report)


def test_s4b_nonzero_exit_but_down_is_stopped() -> None:
    """The command failed, yet the re-observation shows the unit down."""
    world = FakeWorld(stop_error=failed("stop"))
    world.run_unit(100)
    report = lifecycle(world).stop()
    assert report.outcome is Outcome.STOPPED
    assert_p1_one_outcome(report)


def test_s5_stopped() -> None:
    world = FakeWorld()
    world.run_unit(100)
    report = lifecycle(world).stop()
    assert report.outcome is Outcome.STOPPED
    assert report.previous_pid == 100
    assert report.exit_code == 0
    assert world.signalled == []
    assert_p1_one_outcome(report)


def test_s5b_previous_process_alive_after_unbind_waits() -> None:
    """The manager reports inactive, but its old main process still lives.

    The main process is never signalled; stop waits until its handle exits.
    """
    world = FakeWorld()
    main = world.run_unit(100, on_port=False)
    clock = FakeClock()

    def manager_lets_go() -> None:
        world.unit_active = False
        world.mainpid = None

    world.on_stop = manager_lets_go
    clock.at(1.0, main.exit)
    report = lifecycle(world, clock).stop()
    assert report.outcome is Outcome.STOPPED
    assert clock.monotonic() >= 1.0
    assert_p5_no_signal(world, 100)
    assert_p1_one_outcome(report)


def test_s5c_reused_pid_counts_as_exited() -> None:
    """The kernel hands the old main pid to an unrelated process: still stopped."""
    world = FakeWorld()
    world.run_unit(100, on_port=False)

    def stop_then_reuse() -> None:
        world.unit_stops()
        world.spawn(100, on_port=False)  # a new process, same number

    world.on_stop = stop_then_reuse
    report = lifecycle(world).stop()
    assert report.outcome is Outcome.STOPPED
    assert world.signalled == []
    assert_p1_one_outcome(report)


def test_s6_unmanaged_voxd_terminated_then_stopped() -> None:
    world = FakeWorld()
    world.spawn(200)
    report = lifecycle(world).stop()
    assert report.outcome is Outcome.STOPPED
    assert "killed unmanaged voxd pid 200" in report.detail
    assert world.signalled == [200]
    assert_p1_one_outcome(report)


def test_s6b_voxd_on_other_port_named_not_signalled() -> None:
    world = FakeWorld(off_port=frozenset({900}))
    world.spawn(900, on_port=False)
    world.run_unit(100)
    report = lifecycle(world).stop()
    assert report.outcome is Outcome.STOPPED
    assert "voxd pid 900 on another port was not touched" in report.detail
    assert_p5_no_signal(world, 900, 100)
    assert_p1_one_outcome(report)


def test_s7_invisible_holder_timeout_no_signal() -> None:
    world = FakeWorld(foreign_listener=True)
    report = lifecycle(world).stop()
    assert report.outcome is Outcome.TIMEOUT
    assert "still accepts connections but no visible voxd" in report.detail
    assert world.signalled == []
    assert_p1_one_outcome(report)


def test_s8_timeout_backend_stays_active() -> None:
    world = FakeWorld(stop_takes_effect=False)
    world.run_unit(100)
    report = lifecycle(world).stop()
    assert report.outcome is Outcome.TIMEOUT
    assert "voxd.service is still active after 10s" in report.detail
    assert_p5_no_signal(world, 100)
    assert_p1_one_outcome(report)


def test_s8b_timeout_process_survives() -> None:
    world = FakeWorld()
    world.spawn(200, survives=True)
    report = lifecycle(world).stop()
    assert report.outcome is Outcome.TIMEOUT
    assert "voxd pid 200 is still running" in report.detail
    assert world.signalled == [200], "a survivor is signalled once, not every tick"
    assert_p1_one_outcome(report)


def test_stop_deactivating_unit_is_not_already_stopped() -> None:
    """F6: S2 never fires while the manager still reports a main process."""
    world = FakeWorld()
    world.run_unit(100, on_port=False)
    world.unit_active = False
    report = lifecycle(world).stop()
    assert report.outcome is not Outcome.ALREADY_STOPPED
    assert "request_stop" in world.calls
    assert_p1_one_outcome(report)


def test_stop_never_signals_manager_main_process() -> None:
    """F6, P5: a stopping unit's main process on 8421 is not an unmanaged voxd."""
    world = FakeWorld(stop_takes_effect=False)
    world.run_unit(100)
    world.unit_active = False  # deactivating, still holding the port
    report = lifecycle(world).stop()
    assert report.outcome is Outcome.TIMEOUT
    assert_p5_no_signal(world, 100)
    assert_p1_one_outcome(report)


def test_stop_kills_captured_process_off_port_not_exited() -> None:
    """F2: a captured voxd that released 8421 but did not exit is terminated."""
    world = FakeWorld()
    stray = world.spawn(200)
    world.on_stop = stray.unbind
    report = lifecycle(world).stop()
    assert report.outcome is Outcome.STOPPED
    assert world.signalled == [200]
    assert_p1_one_outcome(report)


def test_stop_permission_denied_but_unit_went_down_is_stopped() -> None:
    """F1: a refused request while another actor stopped the unit carries on."""
    world = FakeWorld(stop_error=denied())
    world.run_unit(100)
    report = lifecycle(world).stop()
    assert report.outcome is Outcome.STOPPED
    assert_p1_one_outcome(report)


# -- round 2 ---------------------------------------------------------------


def test_s6_kill_named_once_though_handles_are_reopened() -> None:
    """A1: the probe reopens handles every tick; the kill is still one kill."""
    world = FakeWorld()
    world.spawn(200)
    report = lifecycle(world).stop()
    assert report.outcome is Outcome.STOPPED
    assert report.detail.count("200") == 1
    assert world.signalled == [200]


def test_stop_never_signals_a_main_process_started_mid_tick() -> None:
    """A2, P5: a start between the activity read and the holder list is honored.

    The manager's new main process 600 lands on the port after stop read the
    unit inactive; it is never signalled, and neither is anything else while
    the unit is active.
    """
    world = FakeWorld()
    world.spawn(200)

    def third_party_start() -> None:
        world.run_unit(600)

    world.before_holders = third_party_start
    world.stop_takes_effect = False
    report = lifecycle(world).stop()
    assert_p5_no_signal(world, 600)
    assert world.signalled == []
    assert report.outcome is Outcome.TIMEOUT
    assert_p1_one_outcome(report)


def test_stop_never_signals_a_process_in_the_service_cgroup() -> None:
    """A2, P5: a process systemd still counts as voxd.service is not unmanaged."""
    world = FakeWorld(service_pids={300})
    world.spawn(300)
    report = lifecycle(world).stop()
    assert world.signalled == []
    assert report.outcome is Outcome.TIMEOUT
    assert "300" in report.detail
    assert_p1_one_outcome(report)


def test_stop_query_failure_is_backend_failed_not_a_traceback() -> None:
    """A3: a failing manager query is a report, never an escaping exception."""
    world = FakeWorld(query_error=failed("show"))
    world.run_unit(100)
    report = lifecycle(world).stop()
    assert report.outcome is Outcome.BACKEND_FAILED
    assert "systemctl show voxd exited 1" in report.detail
    assert report.to_json()["outcome"] == "backend_failed"
    assert_p1_one_outcome(report)


def test_stop_query_failure_after_request_names_the_request() -> None:
    """A3: the detail says the stop was already requested when the query broke."""
    world = FakeWorld()
    world.run_unit(100)

    def stop_then_break() -> None:
        world.unit_stops()
        world.query_error = failed("show")

    world.on_stop = stop_then_break
    report = lifecycle(world).stop()
    assert report.outcome is Outcome.BACKEND_FAILED
    assert "after requesting stop" in report.detail
    assert_p1_one_outcome(report)


def test_stop_probe_failure_mid_wait_is_backend_failed() -> None:
    world = FakeWorld()
    world.run_unit(100)

    def stop_then_break_probe() -> None:
        world.unit_stops()
        world.probe_error = "cannot run fuser: not found"

    world.on_stop = stop_then_break_probe
    report = lifecycle(world).stop()
    assert report.outcome is Outcome.BACKEND_FAILED
    assert "fuser" in report.detail


def test_s6_removes_serve_port_naming_8421() -> None:
    """B6: the killed unmanaged voxd's serve.port is removed when it names 8421."""
    world = FakeWorld(port_file=8421)
    world.spawn(200)
    assert lifecycle(world).stop().outcome is Outcome.STOPPED
    assert world.port_file is None


def test_s6_keeps_serve_port_naming_another_port() -> None:
    world = FakeWorld(port_file=9000)
    world.spawn(200)
    assert lifecycle(world).stop().outcome is Outcome.STOPPED
    assert world.port_file == 9000


def test_s6_inactive_unit_needs_no_privilege() -> None:
    """B7: only an unmanaged voxd holds 8421, so stop asks no one for sudo."""
    world = FakeWorld(stop_takes_effect=False, stop_error=denied())
    world.spawn(200)
    report = lifecycle(world).stop()
    assert report.outcome is Outcome.STOPPED
    assert "request_stop" not in world.calls
    assert world.signalled == [200]


def test_stop_checks_installed_before_querying_the_manager() -> None:
    """B8: an absent unit is not_installed even when systemctl show fails."""
    world = FakeWorld(installed=False, query_error=failed("show"))
    report = lifecycle(world).stop()
    assert report.outcome is Outcome.NOT_INSTALLED


def test_stop_overruled_refusal_is_carried_into_the_detail() -> None:
    """B10: a refusal the re-observation contradicts is reported, not dropped."""
    world = FakeWorld(stop_error=denied())
    world.run_unit(100)
    report = lifecycle(world).stop()
    assert report.outcome is Outcome.STOPPED
    assert "password is required" in report.detail


def test_terminate_is_bounded_by_the_remaining_deadline() -> None:
    """B14: escalating at t=4 gives the kill at most the 6 s that remain."""
    world = FakeWorld()
    main = world.run_unit(100, on_port=False)
    world.spawn(200, survives=True)
    world.stop_takes_effect = False
    clock = FakeClock()

    def manager_stops() -> None:
        main.exit()
        world.unit_active = False
        world.mainpid = None

    clock.at(4.0, manager_stops)
    report = lifecycle(world, clock).stop()
    assert report.outcome is Outcome.TIMEOUT
    assert world.budgets
    assert 0 < world.budgets[0] <= 6.0 + 0.21
    assert world.signalled == [200]
