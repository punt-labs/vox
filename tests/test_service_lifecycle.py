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
    assert_p1_one_outcome,
    assert_p2_start_after_exit,
    assert_p5_no_signal,
    denied,
    failed,
    lifecycle,
)

from punt_vox.service.installer import ServiceInstaller
from punt_vox.service.lifecycle import DaemonLifecycle
from punt_vox.service.lifecycle_report import (
    LifecycleOutcome as Outcome,
    LifecycleRefusedError,
)


def test_restart_never_starts_after_failed_stop() -> None:
    world = FakeWorld(stop_takes_effect=False, stop_error=denied())
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


def test_restart_foreign_daemon_in_gap_is_backendfailed() -> None:
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
    world = FakeWorld(start_takes_effect=False, start_error=failed("start"))
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


def test_probe_failure_precedes_not_installed() -> None:
    """F0: a broken probe is backend_failed even where nothing is installed."""
    world = FakeWorld(installed=False, probe_error="cannot run fuser: not found")
    for report in (lifecycle(world).stop(), lifecycle(world).start()):
        assert report.outcome is Outcome.BACKEND_FAILED
        assert_p1_one_outcome(report)


@pytest.mark.parametrize("verb", ["stop", "start", "restart"])
def test_probe_tool_missing_is_backendfailed(verb: str) -> None:
    world = FakeWorld(probe_error="cannot run fuser: not found (install psmisc)")
    world.run_unit(100)
    report = getattr(lifecycle(world), verb)()
    assert report.outcome is Outcome.BACKEND_FAILED
    assert "fuser" in report.detail
    assert "request_stop" not in world.calls
    assert "request_start" not in world.calls
    assert_p1_one_outcome(report)


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


def test_restart_carries_stop_detail() -> None:
    """B15: restart's report keeps what its stop half did."""
    world = FakeWorld(port_file=8421)
    world.spawn(200)
    report = lifecycle(world).restart()
    assert report.outcome is Outcome.RESTARTED
    assert "killed unmanaged voxd pid 200" in report.detail
    assert "killed unmanaged voxd pid 200" in report.text()
    assert "killed unmanaged voxd pid 200" in report.to_json()["detail"]
