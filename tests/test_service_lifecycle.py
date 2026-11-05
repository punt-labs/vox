"""Row-by-row tests of ``vox daemon stop|start|restart`` (design section 4).

One test per outcome row, named for it, on fakes and a fake clock. The model
in ``docs/daemon-lifecycle.tex`` states five properties; the helpers below are
named for them and every test asserts the ones its row can reach:

- **P1** every observation yields exactly one outcome per verb, the same one
  every time, from the verb's own closed set;
- **P2** inside ``restart``, start never acts while a previous or main
  process is alive;
- **P3** ``restart`` never reports ``restarted`` for a stale or foreign daemon;
- **P4** the token goes only to a visible voxd on 8421;
- **P5** no signal reaches the manager's main process or an off-port voxd.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from _lifecycle_fakes import (
    STALE,
    WHEEL,
    FakeClock,
    FakeWorld,
    lifecycle,
)

from punt_vox.service.backend import BackendCommandError, PrivilegeDeniedError
from punt_vox.service.installer import ServiceInstaller
from punt_vox.service.lifecycle import DaemonLifecycle
from punt_vox.service.lifecycle_report import (
    LifecycleOutcome as Outcome,
    LifecycleRefusedError,
    LifecycleReport,
)

_ALLOWED: dict[str, frozenset[Outcome]] = {
    "stop": frozenset(
        {
            Outcome.STOPPED,
            Outcome.ALREADY_STOPPED,
            Outcome.NOT_INSTALLED,
            Outcome.PERMISSION_DENIED,
            Outcome.BACKEND_FAILED,
            Outcome.TIMEOUT,
        }
    ),
    "start": frozenset(
        {
            Outcome.STARTED,
            Outcome.ALREADY_RUNNING,
            Outcome.NOT_INSTALLED,
            Outcome.PERMISSION_DENIED,
            Outcome.BACKEND_FAILED,
            Outcome.TIMEOUT,
            Outcome.PORT_CONFLICT,
            Outcome.VERSION_MISMATCH,
        }
    ),
    "restart": frozenset(
        {
            Outcome.RESTARTED,
            Outcome.NOT_INSTALLED,
            Outcome.PERMISSION_DENIED,
            Outcome.BACKEND_FAILED,
            Outcome.TIMEOUT,
            Outcome.PORT_CONFLICT,
            Outcome.VERSION_MISMATCH,
        }
    ),
}


def _denied() -> PrivilegeDeniedError:
    return PrivilegeDeniedError("sudo: a password is required")


def _failed(verb: str) -> BackendCommandError:
    return BackendCommandError.of(["systemctl", verb, "voxd"], 1, "Job failed")


# -- the modeled properties ------------------------------------------------


def assert_p1_one_outcome(report: LifecycleReport) -> None:
    """P1 (and the section 4.4 table): the outcome is in the verb's closed set."""
    assert report.outcome in _ALLOWED[report.verb]
    assert report.exit_code == (0 if report.outcome.ok else 1)


def assert_p2_start_after_exit(world: FakeWorld) -> None:
    """P2: when start acted, no previous or main process was alive."""
    assert world.alive_at_start == []


def assert_p4_token_only_to_visible_voxd(world: FakeWorld, *allowed: int) -> None:
    """P4: every token went to a visible voxd holding 8421, and only to it."""
    for holders in world.tokens_to:
        assert holders, "a token went out with no visible voxd on the port"
        assert holders <= set(allowed)


def assert_p5_no_signal(world: FakeWorld, *forbidden: int) -> None:
    """P5: no signal reached the manager's main process or an off-port voxd."""
    assert not set(world.signalled) & set(forbidden)


# -- stop ------------------------------------------------------------------


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


def test_s3_permission_denied() -> None:
    world = FakeWorld(stop_takes_effect=False, stop_error=_denied())
    world.run_unit(100)
    report = lifecycle(world).stop()
    assert report.outcome is Outcome.PERMISSION_DENIED
    assert "password is required" in report.detail
    assert report.previous_pid == 100
    assert_p5_no_signal(world, 100)
    assert_p1_one_outcome(report)


def test_s4_backend_failed() -> None:
    world = FakeWorld(stop_takes_effect=False, stop_error=_failed("stop"))
    world.run_unit(100)
    report = lifecycle(world).stop()
    assert report.outcome is Outcome.BACKEND_FAILED
    assert "systemctl stop voxd exited 1: Job failed" in report.detail
    assert_p5_no_signal(world, 100)
    assert_p1_one_outcome(report)


def test_s4b_nonzero_exit_but_down_is_stopped() -> None:
    """The command failed, yet the re-observation shows the unit down."""
    world = FakeWorld(stop_error=_failed("stop"))
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


# -- start -----------------------------------------------------------------


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


def test_t2c_pid_not_mainpid_backend_failed() -> None:
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


def test_t5_permission_denied() -> None:
    world = FakeWorld(start_takes_effect=False, start_error=_denied())
    report = lifecycle(world).start()
    assert report.outcome is Outcome.PERMISSION_DENIED
    assert_p1_one_outcome(report)


def test_t6_backend_failed() -> None:
    world = FakeWorld(start_takes_effect=False, start_error=_failed("start"))
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


def test_t8_foreign_answer_backend_failed() -> None:
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


# -- restart ---------------------------------------------------------------


def test_restart_never_starts_after_failed_stop() -> None:
    world = FakeWorld(stop_takes_effect=False, stop_error=_denied())
    world.run_unit(100)
    report = lifecycle(world).restart()
    assert report.verb == "restart"
    assert report.outcome is Outcome.PERMISSION_DENIED
    assert report.detail.startswith("stop: ")
    assert "request_start" not in world.calls
    assert_p1_one_outcome(report)


def test_restart_starts_only_after_previous_exited() -> None:
    world = FakeWorld()
    main = world.run_unit(100)
    clock = FakeClock()

    def slow_exit() -> None:
        world.unit_active = False
        world.mainpid = None
        main.unbind()

    world.on_stop = slow_exit
    clock.at(1.0, main.exit)
    report = lifecycle(world, clock).restart()
    assert report.outcome is Outcome.RESTARTED
    assert_p2_start_after_exit(world)
    assert_p1_one_outcome(report)


def test_restart_from_stopped_reports_restarted() -> None:
    world = FakeWorld()
    report = lifecycle(world).restart()
    assert report.outcome is Outcome.RESTARTED
    assert (report.pid, report.previous_pid) == (500, None)
    assert_p2_start_after_exit(world)
    assert_p1_one_outcome(report)


def test_restart_reports_previous_and_new_pid() -> None:
    world = FakeWorld()
    world.run_unit(100)
    report = lifecycle(world).restart()
    assert report.outcome is Outcome.RESTARTED
    assert (report.pid, report.previous_pid, report.daemon_version) == (
        500,
        100,
        WHEEL,
    )
    assert_p2_start_after_exit(world)
    assert_p1_one_outcome(report)


def _third_party_starts(world: FakeWorld, version: str) -> Callable[[], None]:
    def start() -> None:
        world.run_unit(700, version=version)

    return start


def test_restart_third_party_start_requires_fresh() -> None:
    """A third party starts the wheel between the halves: that is a restart."""
    world = FakeWorld()
    world.run_unit(100)
    world.between = _third_party_starts(world, WHEEL)
    report = lifecycle(world).restart()
    assert report.outcome is Outcome.RESTARTED
    assert report.pid == 700
    # P3: the serving process is the manager's, on the wheel, not the previous.
    assert report.pid != report.previous_pid
    assert report.daemon_version == WHEEL
    assert "request_start" not in world.calls
    assert_p1_one_outcome(report)


def test_restart_running_stale_version_is_version_mismatch() -> None:
    """P3: a stale daemon started in the gap is never reported restarted."""
    world = FakeWorld()
    world.run_unit(100)
    world.between = _third_party_starts(world, STALE)
    report = lifecycle(world).restart()
    assert report.outcome is Outcome.VERSION_MISMATCH
    assert report.verb == "restart"
    assert_p1_one_outcome(report)


def test_restart_foreign_daemon_in_gap_is_backend_failed() -> None:
    """P3: an unmanaged voxd answering in the gap is never reported restarted."""
    world = FakeWorld()
    world.run_unit(100)

    def foreign() -> None:
        world.run_unit(700, on_port=False)
        world.spawn(800)

    world.between = foreign
    report = lifecycle(world).restart()
    assert report.outcome is Outcome.BACKEND_FAILED
    assert_p1_one_outcome(report)


def test_restart_start_failure_prefixed_start() -> None:
    world = FakeWorld(start_takes_effect=False, start_error=_failed("start"))
    world.run_unit(100)
    report = lifecycle(world).restart()
    assert report.outcome is Outcome.BACKEND_FAILED
    assert report.detail.startswith("start: ")
    assert_p1_one_outcome(report)


def test_restart_waits_for_deactivating_main_process() -> None:
    """F6: a unit whose manager still reports a main process has not stopped."""
    world = FakeWorld(stop_takes_effect=False)
    main = world.run_unit(100, on_port=False)
    world.unit_active = False  # stopping: inactive, main pid still reported
    clock = FakeClock()

    def stop_finishes() -> None:
        main.exit()
        world.mainpid = None

    clock.at(1.0, stop_finishes)
    report = lifecycle(world, clock).restart()
    assert report.outcome is Outcome.RESTARTED
    assert_p2_start_after_exit(world)
    assert_p5_no_signal(world, 100)


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


def test_start_serving_without_main_pid_is_backend_failed() -> None:
    """F3: the manager is re-activating (no main pid) and a stray voxd answers."""
    world = FakeWorld()
    world.unit_active = True
    world.spawn(200)
    report = lifecycle(world).start()
    assert report.outcome is Outcome.BACKEND_FAILED
    assert "answered by pid 200, service manager runs no process" in report.detail
    assert_p1_one_outcome(report)


def test_stop_permission_denied_but_unit_went_down_is_stopped() -> None:
    """F1: a refused request while another actor stopped the unit carries on."""
    world = FakeWorld(stop_error=_denied())
    world.run_unit(100)
    report = lifecycle(world).stop()
    assert report.outcome is Outcome.STOPPED
    assert_p1_one_outcome(report)


def test_start_permission_denied_but_unit_came_up_is_started() -> None:
    """F1 mirrored: a refused start while a third party started the unit."""
    world = FakeWorld(start_error=_denied())
    report = lifecycle(world).start()
    assert report.outcome is Outcome.STARTED
    assert_p1_one_outcome(report)


def test_probe_failure_precedes_not_installed() -> None:
    """F0: a broken probe is backend_failed even where nothing is installed."""
    world = FakeWorld(installed=False, probe_error="cannot run fuser: not found")
    for report in (lifecycle(world).stop(), lifecycle(world).start()):
        assert report.outcome is Outcome.BACKEND_FAILED
        assert_p1_one_outcome(report)


# -- probe failure, every verb ---------------------------------------------


@pytest.mark.parametrize("verb", ["stop", "start", "restart"])
def test_probe_tool_missing_is_backend_failed(verb: str) -> None:
    world = FakeWorld(probe_error="cannot run fuser: not found (install psmisc)")
    world.run_unit(100)
    report = getattr(lifecycle(world), verb)()
    assert report.outcome is Outcome.BACKEND_FAILED
    assert "fuser" in report.detail
    assert "request_stop" not in world.calls
    assert "request_start" not in world.calls
    assert_p1_one_outcome(report)


# -- P1 across many observations -------------------------------------------


def _scenes() -> list[Callable[[FakeWorld], None]]:
    def nothing(world: FakeWorld) -> None:
        del world

    def running(world: FakeWorld) -> None:
        world.run_unit(100)

    def stale(world: FakeWorld) -> None:
        world.run_unit(100, version=STALE)

    def unmanaged(world: FakeWorld) -> None:
        world.spawn(200)

    def invisible(world: FakeWorld) -> None:
        world.foreign_listener = True

    def uninstalled(world: FakeWorld) -> None:
        world.installed = False
        world.spawn(200)

    return [nothing, running, stale, unmanaged, invisible, uninstalled]


@pytest.mark.parametrize("scene", _scenes(), ids=lambda s: s.__name__)
@pytest.mark.parametrize("verb", ["stop", "start", "restart"])
def test_p1_every_observation_yields_one_outcome(
    scene: Callable[[FakeWorld], None], verb: str
) -> None:
    """P1: the same observation gives the same single outcome every time."""
    outcomes = []
    for _ in range(2):
        world = FakeWorld()
        scene(world)
        report = getattr(lifecycle(world), verb)()
        assert_p1_one_outcome(report)
        outcomes.append(report.outcome)
    assert outcomes[0] is outcomes[1]


# -- preconditions ---------------------------------------------------------


def _forbid_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    def touched(plat: str) -> None:
        msg = "a refused verb built a backend"
        raise AssertionError(msg)

    monkeypatch.setattr(ServiceInstaller, "backend_for", staticmethod(touched))


def test_root_refused_before_touching_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    _forbid_backend(monkeypatch)
    monkeypatch.setattr("punt_vox.service.lifecycle_system.os.geteuid", lambda: 0)
    with pytest.raises(LifecycleRefusedError, match="not root or sudo"):
        DaemonLifecycle.for_this_host()


def test_unsupported_platform_refused_before_touching_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _forbid_backend(monkeypatch)
    monkeypatch.setattr("punt_vox.service.lifecycle_system.os.geteuid", lambda: 1000)
    monkeypatch.setattr(
        "punt_vox.service.lifecycle_system.platform.system", lambda: "Windows"
    )
    with pytest.raises(LifecycleRefusedError, match="not Windows"):
        DaemonLifecycle.for_this_host()
