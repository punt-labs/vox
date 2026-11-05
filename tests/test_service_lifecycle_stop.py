"""Row-by-row tests of ``vox daemon stop`` (design section 4.1).

One test per outcome row, named for it, on fakes and a fake clock; each
asserts the model properties (P1, P5) its row can reach.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from _lifecycle_fakes import (
    FakeClock,
    FakeHandle,
    FakeProcess,
    FakeWorld,
    assert_p1_one_outcome,
    assert_p5_no_signal,
    denied,
    failed,
    lifecycle,
)

from punt_vox.service.backend import PortProbeError
from punt_vox.service.clock import Poll
from punt_vox.service.lifecycle_report import LifecycleOutcome as Outcome
from punt_vox.service.termination import Termination

if TYPE_CHECKING:
    from collections.abc import Callable

    import pytest


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
    """A2, P5: a start between the settle read and the signal is honored.

    The stop request takes the unit down, so ``_settled`` reads it inactive.
    Then -- inside that same tick, as the probe lists the holders -- a third
    party starts the unit with main pid 600 on the port. ``_escalate``'s
    re-read sees it active, so neither 600 nor the unmanaged 200 is signalled.
    """
    world = FakeWorld()
    world.run_unit(100)
    world.spawn(200)

    def third_party_start() -> None:
        world.run_unit(600)

    def stop_then_arm() -> None:
        world.unit_stops()
        world.before_holders = third_party_start

    world.on_stop = stop_then_arm
    report = lifecycle(world).stop()
    assert "request_stop" in world.calls
    assert_p5_no_signal(world, 600, 200)
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


# -- round 3 ---------------------------------------------------------------


def test_slow_request_does_not_eat_the_kill_budget() -> None:
    """R2a: the deadline starts after the request, not before it.

    The stop request blocks 11 s (a sudo prompt); a lingering unmanaged voxd
    must still get a full SIGTERM grace, not a zero budget.
    """
    world = FakeWorld()
    world.run_unit(100)
    world.spawn(200)

    def slow_stop() -> None:
        assert world.clock is not None
        world.clock.advance(11.0)
        world.unit_stops()

    world.on_stop = slow_stop
    report = lifecycle(world).stop()
    assert report.outcome is Outcome.STOPPED
    assert world.budgets
    assert world.budgets[0] >= 9.0


def test_failure_after_a_kill_names_the_kill() -> None:
    """R3: a probe failure mid-escalation still reports what stop already did."""
    world = FakeWorld(
        probe_failures={("service_owned", 201): failed("cgroup read")},
    )
    world.spawn(200)
    world.spawn(201)
    report = lifecycle(world).stop()
    assert report.outcome is Outcome.BACKEND_FAILED
    assert "killed unmanaged voxd pid 200" in report.detail
    assert world.signalled == [200]


def test_failure_after_a_kill_and_a_request_says_both() -> None:
    world = FakeWorld(
        stop_error=denied(),
        probe_failures={("service_owned", 201): failed("cgroup read")},
    )
    world.run_unit(100)
    world.spawn(200)
    world.spawn(201)
    report = lifecycle(world).stop()
    assert report.outcome is Outcome.BACKEND_FAILED
    assert report.detail.startswith("after requesting stop: ")
    assert "killed unmanaged voxd pid 200" in report.detail
    assert "password is required" in report.detail


def test_port_file_cleanup_failure_is_a_warning_not_a_failure() -> None:
    """R3: once the kill succeeded, a serve.port cleanup error only warns."""
    world = FakeWorld(
        port_file=8421,
        probe_failures={"clear_port_file": PortProbeError("cannot read serve.port")},
    )
    world.spawn(200)
    report = lifecycle(world).stop()
    assert report.outcome is Outcome.STOPPED
    assert "killed unmanaged voxd pid 200" in report.detail
    assert "cannot read serve.port" in report.detail


def test_timeout_explains_a_spared_cgroup_member() -> None:
    """R5: a voxd left alone because systemd owns it is named with the reason."""
    world = FakeWorld(service_pids={300})
    world.spawn(300)
    report = lifecycle(world).stop()
    assert report.outcome is Outcome.TIMEOUT
    assert "voxd pid 300 spared: voxd.service still owns it" in report.detail


def test_timeout_explains_a_survivor() -> None:
    """R5: a voxd that outlived terminate() is named with the reason."""
    world = FakeWorld()
    world.spawn(200, survives=True)
    report = lifecycle(world).stop()
    assert report.outcome is Outcome.TIMEOUT
    assert "voxd pid 200 did not exit after SIGTERM/SIGKILL" in report.detail


# -- the report reads only what was recorded when each signal was sent -------


def _probes_break_after_the_wait(
    monkeypatch: pytest.MonkeyPatch, world: FakeWorld
) -> None:
    """Once the settle poll returns, every probe and manager query fails.

    A report built from the outcomes recorded at signal time is untouched by
    this; a report that probes again turns into a failure or a traceback.
    """
    broken = {"now": False}
    real_until = Poll.until
    real_exited = FakeHandle.exited

    def until(self: Poll, condition: Callable[[], bool], deadline_s: float) -> bool:
        done = real_until(self, condition, deadline_s)
        broken["now"] = True
        world.probe_error = "ps timed out"
        world.query_error = failed("show")
        world.probe_failures["service_owned"] = PortProbeError("cgroup read")
        return done

    def exited(self: FakeHandle) -> bool:
        if broken["now"]:
            raise PortProbeError("cannot inspect pid: ps timed out")
        return real_exited(self)

    monkeypatch.setattr(Poll, "until", until)
    monkeypatch.setattr(FakeHandle, "exited", exited)


def test_probe_failure_after_a_signal_names_every_signalled_pid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """djb B1: exited() and service_owned(201) fail after 200 was terminated.

    The failure is backend_failed, never a traceback, and its detail names
    200 -- the pid stop signalled -- from the record made when it signalled.
    """
    state = {"boom": False}
    real_terminate = FakeProcess.terminate
    real_exited = FakeHandle.exited

    def terminate(self: FakeProcess, within_s: float) -> Termination:
        outcome = real_terminate(self, within_s)
        state["boom"] = True
        return outcome

    def exited(self: FakeHandle) -> bool:
        if state["boom"]:
            raise PortProbeError("cannot inspect pid: ps timed out")
        return real_exited(self)

    monkeypatch.setattr(FakeProcess, "terminate", terminate)
    monkeypatch.setattr(FakeHandle, "exited", exited)
    world = FakeWorld(probe_failures={("service_owned", 201): failed("cgroup read")})
    world.spawn(200, survives=True)
    world.spawn(201)
    report = lifecycle(world).stop()
    assert report.outcome is Outcome.BACKEND_FAILED
    assert "cgroup read" in report.detail
    assert "voxd pid 200 did not exit after SIGTERM/SIGKILL" in report.detail
    assert world.signalled == [200]
    assert_p1_one_outcome(report)


def test_timeout_report_runs_no_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """The timeout detail comes from the last tick and the signal records."""
    world = FakeWorld()
    world.spawn(200, survives=True)
    _probes_break_after_the_wait(monkeypatch, world)
    report = lifecycle(world).stop()
    assert report.outcome is Outcome.TIMEOUT
    assert "voxd pid 200 is still running" in report.detail
    assert "voxd pid 200 did not exit after SIGTERM/SIGKILL" in report.detail


def test_stopped_report_runs_no_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    world = FakeWorld()
    world.spawn(200)
    _probes_break_after_the_wait(monkeypatch, world)
    report = lifecycle(world).stop()
    assert report.outcome is Outcome.STOPPED
    assert "killed unmanaged voxd pid 200" in report.detail


def _sigterm_only_at_the_deadline(world: FakeWorld) -> FakeClock:
    """The manager lets go at 9.3 s, leaving well under the 1 s kill floor."""
    main = world.run_unit(100, on_port=False)
    world.stop_takes_effect = False
    clock = FakeClock()

    def manager_stops() -> None:
        main.exit()
        world.unit_active = False
        world.mainpid = None

    clock.at(9.3, manager_stops)
    return clock


def test_sigterm_only_kill_that_exits_on_a_later_tick_is_named_killed() -> None:
    """A below-floor terminate gets SIGTERM only; its exit is seen next tick."""
    world = FakeWorld()
    clock = _sigterm_only_at_the_deadline(world)
    world.spawn(200, slow_exit=True)
    report = lifecycle(world, clock).stop()
    assert world.budgets
    assert world.budgets[0] < 1.0
    assert report.outcome is Outcome.STOPPED
    assert "killed unmanaged voxd pid 200" in report.detail
    assert_p5_no_signal(world, 100)


def test_sigterm_only_survivor_is_explained_by_the_deadline() -> None:
    """The reason is the missing grace period, not "ignored the signals"."""
    world = FakeWorld()
    clock = _sigterm_only_at_the_deadline(world)
    world.spawn(200, survives=True)
    report = lifecycle(world, clock).stop()
    assert report.outcome is Outcome.TIMEOUT
    assert "voxd pid 200 got SIGTERM only" in report.detail
    assert "no time for a grace period" in report.detail
    assert "ignored the signals" not in report.detail


def test_port_file_cleanup_runs_once_and_notes_a_failure_once() -> None:
    """A spared voxd keeps the stop ticking; the cleanup still runs once."""
    world = FakeWorld(
        port_file=8421,
        service_pids={300},
        probe_failures={"clear_port_file": PortProbeError("unlink denied")},
    )
    world.spawn(200)
    spared = world.spawn(300)
    clock = FakeClock()
    clock.at(0.1, spared.unbind)  # leaves 8421, stays alive in the cgroup
    report = lifecycle(world, clock).stop()
    assert report.outcome is Outcome.TIMEOUT
    assert world.calls.count("clear_port_file") == 1
    assert report.detail.count("could not clear serve.port") == 1
    assert world.signalled == [200]


def test_port_file_kept_while_a_visible_voxd_holds_the_port() -> None:
    """The serve.port of a voxd still on 8421 is not removed under it."""
    world = FakeWorld(port_file=8421, service_pids={300})
    world.spawn(200)
    world.spawn(300)
    lifecycle(world).stop()
    assert world.port_file == 8421
    assert "clear_port_file" not in world.calls


def test_port_file_cleanup_waits_for_a_confirmed_exit() -> None:
    """No serve.port is removed while every signalled voxd may still be up."""
    world = FakeWorld(port_file=8421)
    world.spawn(200, survives=True)
    world.spawn(201, on_port=False, survives=True)
    world.procs[200].unbind()
    lifecycle(world).stop()
    assert world.port_file == 8421


def test_unparseable_port_file_is_removed_after_the_kill() -> None:
    world = FakeWorld(port_file_stale=True)
    world.spawn(200)
    assert lifecycle(world).stop().outcome is Outcome.STOPPED
    assert world.port_file_stale is False


def test_spared_pid_is_never_also_reported_killed() -> None:
    """A voxd spared as the service's is not signalled once it leaves the cgroup."""
    world = FakeWorld(service_pids={300})
    world.spawn(300)
    clock = FakeClock()
    clock.at(1.0, world.service_pids.clear)
    report = lifecycle(world, clock).stop()
    assert world.signalled == []
    assert "voxd pid 300 spared" in report.detail
    assert "killed unmanaged voxd pid 300" not in report.detail


def test_manager_is_asked_again_before_each_signal() -> None:
    """A unit started while 200 is being killed spares 201 in the same tick."""
    world = FakeWorld()
    world.spawn(200)
    world.spawn(201)

    def third_party_start(pid: int) -> None:
        if pid == 200:
            world.run_unit(600)

    world.on_signal = third_party_start
    report = lifecycle(world).stop()
    assert world.signalled == [200]
    assert_p5_no_signal(world, 201, 600)
    assert report.outcome is Outcome.TIMEOUT
    assert "killed unmanaged voxd pid 200" in report.detail


# -- round 2 of the reporting mission ----------------------------------------


def test_port_file_cleanup_declined_for_a_new_holder_is_retried() -> None:
    """S3: a voxd that appears just before the clear defers it, not spends it.

    The cleanup runs again on a later tick, after that voxd is killed and
    confirmed gone, and the file is removed then.
    """
    world = FakeWorld(port_file=8421)
    world.spawn(200)
    world.before_clear = lambda: world.spawn(250).bind()
    report = lifecycle(world).stop()
    assert world.calls.count("clear_port_file") == 2
    assert world.port_file is None
    assert world.signalled == [200, 250]
    assert report.outcome is Outcome.STOPPED


def test_late_holder_given_sigterm_only_and_gone_next_tick_is_killed() -> None:
    """S4: a voxd that took 8421 after stop observed is still followed.

    It is not among the captured handles; only the ledger's unconfirmed set
    brings it into the next tick's watch, where its exit is confirmed.
    """
    world = FakeWorld()
    main = world.run_unit(100, on_port=False)
    world.stop_takes_effect = False
    clock = FakeClock()

    def manager_stops_and_a_voxd_binds() -> None:
        main.exit()
        world.unit_active = False
        world.mainpid = None
        world.spawn(250, slow_exit=True)

    clock.at(9.3, manager_stops_and_a_voxd_binds)
    report = lifecycle(world, clock).stop()
    assert world.budgets
    assert world.budgets[0] < 1.0
    assert report.outcome is Outcome.STOPPED
    assert "killed unmanaged voxd pid 250" in report.detail
    assert "SIGTERM only" not in report.detail


def test_a_terminate_that_fails_after_signalling_still_names_the_pid() -> None:
    """S5: the ledger entry is written before terminate(), which then raises."""
    world = FakeWorld()
    world.spawn(200)

    def signal_then_fail(pid: int) -> None:
        msg = f"cannot signal voxd pid {pid}: Invalid argument"
        raise PortProbeError(msg)

    world.on_signal = signal_then_fail
    report = lifecycle(world).stop()
    assert report.outcome is Outcome.BACKEND_FAILED
    assert world.signalled == [200]
    assert "Invalid argument" in report.detail
    assert (
        "stop began signalling voxd pid 200; its exit was never confirmed"
        in report.detail
    )


def test_a_refused_signal_is_reported_as_refused() -> None:
    """S9: permission denied is not "got SIGTERM only" nor "ignored"."""
    world = FakeWorld()
    world.spawn(200, refuses=True)
    report = lifecycle(world).stop()
    assert report.outcome is Outcome.TIMEOUT
    assert "voxd pid 200 refused the signal: permission denied" in report.detail
    assert "SIGTERM only" not in report.detail
    assert "ignored the signals" not in report.detail
    assert world.signalled == [200]
