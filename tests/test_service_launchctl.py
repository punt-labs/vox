"""Boundary tests for punt_vox.service.launchctl -- the launchctl subprocess.

These tests mock the ``launchctl`` subprocess (``print``/``bootout``/
``bootstrap``/``kickstart``) and exercise the race that leaves voxd down on a
first ``vox daemon restart``:

* happy path -- clean domain, bootstrap succeeds first try;
* ``bootstrap`` returns exit 5 once, then succeeds after the job unregisters;
* the job never leaves the domain within the bound -- ``bootstrap`` raises so
  the caller reports failure instead of a false "restarted".

No real launchctl is required; the bound is shortened via the class constants so
the failure case runs fast.
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
from _lifecycle_fakes import FakeClock

from punt_vox.service.clock import SystemClock
from punt_vox.service.launchctl import LaunchctlAgent
from punt_vox.service.launchctl_query import LaunchctlError

if TYPE_CHECKING:
    from collections.abc import Callable

_PLIST = "/var/tmp/com.punt-labs.voxd.plist"  # test literal path, never written
_LABEL = "com.punt-labs.voxd"


def _completed(
    returncode: int, stderr: str = "", stdout: str = ""
) -> subprocess.CompletedProcess[str]:
    """Build a CompletedProcess with the given exit code."""
    return subprocess.CompletedProcess(
        args=["launchctl"], returncode=returncode, stdout=stdout, stderr=stderr
    )


def _verb(call_args: object) -> str:
    """Extract the launchctl subcommand (argv[1]) from a recorded call."""
    argv = call_args[0][0]  # type: ignore[index]  # positional args tuple
    return str(argv[1])


@pytest.fixture()
def agent() -> LaunchctlAgent:
    """A LaunchctlAgent bound to a throwaway label/plist."""
    return LaunchctlAgent(_LABEL, _PLIST)


@pytest.fixture()
def fast_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shrink the unregister bound so the never-clears case runs in ~20ms."""
    monkeypatch.setattr(LaunchctlAgent, "_UNREGISTER_TIMEOUT_S", 0.02)
    monkeypatch.setattr(LaunchctlAgent, "_UNREGISTER_POLL_S", 0.005)


def _dispatch(
    print_rc: int,
    bootstrap_rcs: list[int],
    kickstart_rc: int = 0,
    bootout_rc: int = 0,
) -> Callable[..., subprocess.CompletedProcess[str]]:
    """Return a subprocess.run side_effect keyed on the launchctl verb.

    ``print_rc`` is the registration probe result (0 = still registered).
    ``bootstrap_rcs`` is consumed one code per bootstrap call.
    """
    remaining = list(bootstrap_rcs)

    def _side_effect(
        argv: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        verb = argv[1]
        if verb == "print":
            return _completed(print_rc)
        if verb == "bootout":
            return _completed(bootout_rc)
        if verb == "bootstrap":
            return _completed(remaining.pop(0) if remaining else 0, "boot error")
        if verb == "kickstart":
            return _completed(kickstart_rc, "kick error")
        raise AssertionError(f"unexpected launchctl verb: {verb}")

    return _side_effect


# ---------------------------------------------------------------------------
# is_registered / wait_until_unregistered
# ---------------------------------------------------------------------------


@patch("punt_vox.service.launchctl_query.subprocess.run")
def test_is_registered_true_when_print_exits_zero(
    mock_run: MagicMock, agent: LaunchctlAgent
) -> None:
    """launchctl print exit 0 means the job is still registered."""
    mock_run.return_value = _completed(0)
    assert agent.is_registered() is True


@patch("punt_vox.service.launchctl_query.subprocess.run")
def test_is_registered_false_when_print_nonzero(
    mock_run: MagicMock, agent: LaunchctlAgent
) -> None:
    """launchctl print exit 113 ("could not find service") means it left."""
    mock_run.return_value = _completed(113)
    assert agent.is_registered() is False


@patch("punt_vox.service.launchctl_query.subprocess.run")
def test_is_loaded_true_when_list_exits_zero(
    mock_run: MagicMock, agent: LaunchctlAgent
) -> None:
    """launchctl list exit 0 means the job is loaded under launchd."""
    mock_run.return_value = _completed(0)
    assert agent.is_loaded() is True
    assert _verb(mock_run.call_args_list[0]) == "list"


@patch("punt_vox.service.launchctl_query.subprocess.run")
def test_is_loaded_false_when_list_nonzero(
    mock_run: MagicMock, agent: LaunchctlAgent
) -> None:
    """launchctl list exit 113 means the job is not loaded."""
    mock_run.return_value = _completed(113)
    assert agent.is_loaded() is False


@patch("punt_vox.service.launchctl_query.subprocess.run")
def test_wait_returns_immediately_when_already_unregistered(
    mock_run: MagicMock, agent: LaunchctlAgent
) -> None:
    """A clear domain returns True on the first poll without sleeping."""
    mock_run.return_value = _completed(113)  # not registered
    with patch("punt_vox.service.clock.time.sleep") as mock_sleep:
        assert agent.wait_until_unregistered().holds is True
    mock_sleep.assert_not_called()


@patch("punt_vox.service.launchctl_query.subprocess.run")
def test_wait_times_out_when_job_never_clears(
    mock_run: MagicMock, agent: LaunchctlAgent, fast_bound: None
) -> None:
    """A job that stays registered makes the bounded wait report failure."""
    mock_run.return_value = _completed(0)  # always registered
    assert agent.wait_until_unregistered().holds is False


# ---------------------------------------------------------------------------
# bootout -- boots out, then waits for the domain to clear
# ---------------------------------------------------------------------------


@patch("punt_vox.service.launchctl_query.subprocess.run")
def test_bootout_waits_for_unregister(
    mock_run: MagicMock, agent: LaunchctlAgent
) -> None:
    """bootout issues the bootout then confirms the job left the domain."""
    mock_run.side_effect = _dispatch(print_rc=113, bootstrap_rcs=[])
    agent.bootout()
    verbs = [_verb(c) for c in mock_run.call_args_list]
    assert "bootout" in verbs
    assert "print" in verbs[verbs.index("bootout") :]  # the wait polled after
    # bootout targets the GUI domain and never shells out to sudo.
    for call in mock_run.call_args_list:
        assert call[0][0][0] == "launchctl"


@patch("punt_vox.service.launchctl_query.subprocess.run")
def test_bootout_of_loaded_job_failing_raises(
    mock_run: MagicMock, agent: LaunchctlAgent
) -> None:
    """A loaded job whose bootout exits non-zero is not reported as stopped."""
    mock_run.side_effect = _dispatch(print_rc=0, bootstrap_rcs=[], bootout_rc=37)
    with pytest.raises(LaunchctlError, match=r"bootout .* failed \(exit 37\)"):
        agent.bootout()


@patch("punt_vox.service.launchctl_query.subprocess.run")
def test_bootout_of_unloaded_job_is_idempotent(
    mock_run: MagicMock, agent: LaunchctlAgent
) -> None:
    """Booting out a job that was never loaded exits non-zero and is ignored."""
    mock_run.side_effect = _dispatch(print_rc=113, bootstrap_rcs=[], bootout_rc=3)
    agent.bootout()  # must not raise


# ---------------------------------------------------------------------------
# pid -- from launchctl print
# ---------------------------------------------------------------------------

_PRINT = "gui/501/com.punt-labs.voxd = {\n\tstate = running\n\tpid = 4242\n}\n"


@patch("punt_vox.service.launchctl_query.subprocess.run")
def test_pid_reads_running_pid(mock_run: MagicMock, agent: LaunchctlAgent) -> None:
    mock_run.return_value = _completed(0, stdout=_PRINT)
    assert agent.pid() == 4242


@patch("punt_vox.service.launchctl_query.subprocess.run")
def test_pid_absent_when_job_has_no_process(
    mock_run: MagicMock, agent: LaunchctlAgent
) -> None:
    mock_run.return_value = _completed(0, stdout="{\n\tstate = waiting\n}\n")
    assert agent.pid() is None


@patch("punt_vox.service.launchctl_query.subprocess.run")
def test_pid_absent_when_unregistered(
    mock_run: MagicMock, agent: LaunchctlAgent
) -> None:
    mock_run.return_value = _completed(113)
    assert agent.pid() is None


@patch("punt_vox.service.launchctl_query.subprocess.run")
def test_pid_malformed_raises(mock_run: MagicMock, agent: LaunchctlAgent) -> None:
    mock_run.return_value = _completed(0, stdout="\tpid = four\n")
    with pytest.raises(LaunchctlError, match="malformed pid"):
        agent.pid()


# ---------------------------------------------------------------------------
# bootstrap -- happy, retry-on-5, and never-unregister failure
# ---------------------------------------------------------------------------


@patch("punt_vox.service.launchctl_query.subprocess.run")
def test_bootstrap_happy_path(mock_run: MagicMock, agent: LaunchctlAgent) -> None:
    """Clean domain, bootstrap succeeds first try -- no error, one bootstrap."""
    mock_run.side_effect = _dispatch(print_rc=113, bootstrap_rcs=[0])
    agent.bootstrap()
    verbs = [_verb(c) for c in mock_run.call_args_list]
    assert verbs.count("bootstrap") == 1
    # bootstrap targets gui/<uid>, never sudo.
    bootstrap_call = next(c for c in mock_run.call_args_list if _verb(c) == "bootstrap")
    assert bootstrap_call[0][0][2].startswith("gui/")


@patch("punt_vox.service.launchctl_query.subprocess.run")
def test_bootstrap_retries_once_on_exit_5(
    mock_run: MagicMock, agent: LaunchctlAgent
) -> None:
    """Exit 5 (stale registration) then success after the job clears."""
    mock_run.side_effect = _dispatch(print_rc=113, bootstrap_rcs=[5, 0])
    agent.bootstrap()  # must not raise
    verbs = [_verb(c) for c in mock_run.call_args_list]
    assert verbs.count("bootstrap") == 2  # first hit 5, retry succeeded


@patch("punt_vox.service.launchctl_query.subprocess.run")
def test_bootstrap_raises_when_job_never_unregisters(
    mock_run: MagicMock, agent: LaunchctlAgent, fast_bound: None
) -> None:
    """Case (c): the job never leaves the domain -- bootstrap reports failure."""
    mock_run.return_value = _completed(0)  # print always says registered
    with pytest.raises(LaunchctlError, match="did not leave"):
        agent.bootstrap()


@patch("punt_vox.service.launchctl_query.subprocess.run")
def test_bootstrap_raises_when_exit_5_persists(
    mock_run: MagicMock, agent: LaunchctlAgent
) -> None:
    """A non-transient bootstrap failure surfaces as LaunchctlError."""
    mock_run.side_effect = _dispatch(print_rc=113, bootstrap_rcs=[5, 5])
    with pytest.raises(LaunchctlError, match="bootstrap"):
        agent.bootstrap()


@patch("punt_vox.service.launchctl_query.subprocess.run")
def test_bootstrap_raises_on_non_io_error(
    mock_run: MagicMock, agent: LaunchctlAgent
) -> None:
    """A non-5 bootstrap exit is not retried and raises immediately."""
    mock_run.side_effect = _dispatch(print_rc=113, bootstrap_rcs=[9])
    with pytest.raises(LaunchctlError, match="exit 9"):
        agent.bootstrap()
    verbs = [_verb(c) for c in mock_run.call_args_list]
    assert verbs.count("bootstrap") == 1  # no retry on a non-5 code


# ---------------------------------------------------------------------------
# start / kickstart
# ---------------------------------------------------------------------------


@patch("punt_vox.service.launchctl_query.subprocess.run")
def test_start_bootstraps_then_kickstarts(
    mock_run: MagicMock, agent: LaunchctlAgent
) -> None:
    """start brings the job up: bootstrap then kickstart, both succeeding."""
    mock_run.side_effect = _dispatch(print_rc=113, bootstrap_rcs=[0], kickstart_rc=0)
    agent.start()
    verbs = [_verb(c) for c in mock_run.call_args_list]
    assert verbs.count("bootstrap") == 1
    assert verbs.count("kickstart") == 1
    assert verbs.index("bootstrap") < verbs.index("kickstart")


@patch("punt_vox.service.launchctl_query.subprocess.run")
def test_kickstart_failure_raises(mock_run: MagicMock, agent: LaunchctlAgent) -> None:
    """A failed kickstart surfaces as LaunchctlError so start() reports it."""
    mock_run.side_effect = _dispatch(print_rc=113, bootstrap_rcs=[0], kickstart_rc=1)
    with pytest.raises(LaunchctlError, match="kickstart"):
        agent.start()


# ---------------------------------------------------------------------------
# round 2: a failed query is never "absent"; every command is bounded
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("probe", ["is_registered", "is_loaded", "pid"])
@patch("punt_vox.service.launchctl_query.subprocess.run")
def test_failed_query_raises_not_absent(
    mock_run: MagicMock, agent: LaunchctlAgent, probe: str
) -> None:
    """B1: only exit 113 means absent; exit 1 is a failed query."""
    mock_run.return_value = _completed(1, "launchctl: bootstrap port error")
    with pytest.raises(LaunchctlError, match="exit 1"):
        getattr(agent, probe)()


@patch(
    "punt_vox.service.launchctl_query.subprocess.run",
    side_effect=subprocess.TimeoutExpired(cmd="launchctl", timeout=10),
)
def test_hung_launchctl_raises(_mock_run: MagicMock, agent: LaunchctlAgent) -> None:
    """B14: a launchctl that hangs is bounded and typed."""
    with pytest.raises(LaunchctlError, match="could not run"):
        agent.is_loaded()


@patch("punt_vox.service.launchctl_query.subprocess.run")
def test_every_launchctl_call_has_a_timeout(
    mock_run: MagicMock, agent: LaunchctlAgent
) -> None:
    mock_run.side_effect = _dispatch(print_rc=113, bootstrap_rcs=[0])
    agent.start()
    agent.bootout()
    assert all(c.kwargs.get("timeout") for c in mock_run.call_args_list)


@patch("punt_vox.service.launchctl_query.subprocess.run")
def test_bootout_of_unloaded_job_does_not_claim_a_bootout(
    mock_run: MagicMock,
    agent: LaunchctlAgent,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """B18: a non-zero bootout of a job that was not loaded logs no "Booted out"."""
    mock_run.side_effect = _dispatch(print_rc=113, bootstrap_rcs=[], bootout_rc=3)
    with caplog.at_level("DEBUG", logger="punt_vox.service.launchctl"):
        agent.bootout()
    assert not any("Booted out" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# a query inside a bounded wait never outlives the wait
# ---------------------------------------------------------------------------


@patch("punt_vox.service.launchctl_query.subprocess.run")
def test_wait_bounds_each_print_by_what_remains_of_the_wait(
    mock_run: MagicMock, agent: LaunchctlAgent, fast_bound: None
) -> None:
    """Each print gets at most the time left in the 20 ms wait, not 10 s."""
    mock_run.return_value = _completed(0)  # always registered
    assert agent.wait_until_unregistered().holds is False
    timeouts = [c.kwargs["timeout"] for c in mock_run.call_args_list]
    assert timeouts
    assert all(0 < t <= LaunchctlAgent._UNREGISTER_TIMEOUT_S for t in timeouts)


@patch(
    "punt_vox.service.launchctl_query.subprocess.run",
    side_effect=subprocess.TimeoutExpired(cmd="launchctl", timeout=0.02),
)
def test_wait_reads_a_print_cut_short_by_the_wait_as_still_registered(
    _mock_run: MagicMock, agent: LaunchctlAgent, fast_bound: None
) -> None:
    """A print that does not answer inside the wait confirms nothing."""
    assert agent.wait_until_unregistered().holds is False


@patch("punt_vox.service.launchctl_query.subprocess.run")
def test_running_within_bounds_both_queries_by_the_budget(
    mock_run: MagicMock, agent: LaunchctlAgent
) -> None:
    """list and print together stay inside the caller's 0.3 s."""
    mock_run.side_effect = [_completed(113), _completed(0, stdout=_PRINT)]
    assert agent.running_within(0.3).holds is True
    timeouts = [c.kwargs["timeout"] for c in mock_run.call_args_list]
    assert [_verb(c) for c in mock_run.call_args_list] == ["list", "print"]
    assert all(0 < t <= 0.3 for t in timeouts)


@patch("punt_vox.service.launchctl_query.subprocess.run")
def test_running_within_false_when_loaded_nowhere(
    mock_run: MagicMock, agent: LaunchctlAgent
) -> None:
    mock_run.return_value = _completed(113)
    assert agent.running_within(1.0).holds is False


@patch(
    "punt_vox.service.launchctl_query.subprocess.run",
    side_effect=subprocess.TimeoutExpired(cmd="launchctl", timeout=0.3),
)
def test_running_within_a_query_cut_short_confirms_nothing(
    _mock_run: MagicMock, agent: LaunchctlAgent
) -> None:
    answer = agent.running_within(0.3)
    assert answer.holds is False
    assert answer.unanswered == f"launchctl list {_LABEL}"


@patch("punt_vox.service.launchctl_query.subprocess.run")
def test_running_within_a_failed_query_still_raises(
    mock_run: MagicMock, agent: LaunchctlAgent
) -> None:
    mock_run.return_value = _completed(1, "launchctl: bootstrap port error")
    with pytest.raises(LaunchctlError, match="exit 1"):
        agent.running_within(1.0)


@patch("punt_vox.service.launchctl_query.subprocess.run")
def test_running_within_gives_print_only_what_list_left(
    mock_run: MagicMock, agent: LaunchctlAgent, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S6: a list that used 0.2 s of a 0.3 s budget leaves print at most 0.1 s."""
    now = [100.0]

    def monotonic(self: SystemClock) -> float:
        return now[0]

    monkeypatch.setattr(SystemClock, "monotonic", monotonic)

    def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if argv[1] == "list":
            now[0] += 0.2
            return _completed(113)
        return _completed(0, stdout=_PRINT)

    mock_run.side_effect = run
    assert agent.running_within(0.3).holds is True
    print_call = mock_run.call_args_list[-1]
    assert _verb(print_call) == "print"
    assert print_call.kwargs["timeout"] <= 0.1 + 1e-9


@patch("punt_vox.service.launchctl_query.subprocess.run")
def test_bootout_confirmed_raises_naming_a_print_that_timed_out(
    mock_run: MagicMock, agent: LaunchctlAgent, fast_bound: None
) -> None:
    """S1: a loaded job whose departure cannot be confirmed is not "gone"."""

    def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if argv[1] == "bootout":
            return _completed(0)
        if mock_run.call_count == 1:  # the was-it-loaded print
            return _completed(0)
        raise subprocess.TimeoutExpired(cmd="launchctl", timeout=0.01)

    mock_run.side_effect = run
    with pytest.raises(LaunchctlError, match="did not answer"):
        agent.bootout_confirmed()


@patch("punt_vox.service.launchctl_query.subprocess.run")
def test_bootout_confirmed_raises_when_the_job_stays_registered(
    mock_run: MagicMock, agent: LaunchctlAgent, fast_bound: None
) -> None:
    mock_run.side_effect = _dispatch(print_rc=0, bootstrap_rcs=[])
    with pytest.raises(LaunchctlError, match="still registered"):
        agent.bootout_confirmed()


@patch("punt_vox.service.launchctl_query.subprocess.run")
def test_bootout_for_stop_only_warns_when_the_job_lingers(
    mock_run: MagicMock, agent: LaunchctlAgent, fast_bound: None
) -> None:
    """stop's request keeps its settle loop: bootout() itself does not raise."""
    mock_run.side_effect = _dispatch(print_rc=0, bootstrap_rcs=[])
    agent.bootout()


@pytest.fixture()
def fake_time(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    """Run the unregister wait on a fake clock: 0.25 s bound, 0.1 s polls."""
    clock = FakeClock()

    def monotonic(self: SystemClock) -> float:
        return clock.monotonic()

    def sleep(self: SystemClock, seconds: float) -> None:
        clock.sleep(seconds)

    monkeypatch.setattr(SystemClock, "monotonic", monotonic)
    monkeypatch.setattr(SystemClock, "sleep", sleep)
    monkeypatch.setattr(LaunchctlAgent, "_UNREGISTER_TIMEOUT_S", 0.25)
    monkeypatch.setattr(LaunchctlAgent, "_UNREGISTER_POLL_S", 0.1)
    return clock


def _registered_then_hung(
    bootstrap_rcs: list[int], *, clear_first: bool
) -> Callable[..., subprocess.CompletedProcess[str]]:
    """print answers "registered" once per wait, then hangs with a full budget.

    With *clear_first*, the first wait sees the job gone; only the wait after
    an exit-5 bootstrap sees it registered and then hangs.
    """
    remaining = list(bootstrap_rcs)
    waits = [0]
    prints_in_wait = [0]

    def side_effect(
        argv: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        if argv[1] == "bootstrap":
            waits[0] += 1
            prints_in_wait[0] = 0
            return _completed(remaining.pop(0) if remaining else 0, "boot error")
        if argv[1] != "print":
            return _completed(0)
        if clear_first and waits[0] == 0:
            return _completed(113)
        prints_in_wait[0] += 1
        if prints_in_wait[0] == 1:
            return _completed(0)
        raise subprocess.TimeoutExpired(cmd="launchctl", timeout=0.15)

    return side_effect


@patch("punt_vox.service.launchctl_query.subprocess.run")
def test_bootstrap_wait_names_a_launchctl_that_answered_then_hung(
    mock_run: MagicMock, agent: LaunchctlAgent, fake_time: FakeClock
) -> None:
    """The wait before bootstrap: "registered", then print hangs."""
    mock_run.side_effect = _registered_then_hung([], clear_first=False)
    with pytest.raises(LaunchctlError, match="last seen still registered") as info:
        agent.bootstrap()
    assert "stopped answering before the 0.25s wait before bootstrap ended" in str(
        info.value
    )


@patch("punt_vox.service.launchctl_query.subprocess.run")
def test_exit5_retry_wait_names_a_launchctl_that_answered_then_hung(
    mock_run: MagicMock, agent: LaunchctlAgent, fake_time: FakeClock
) -> None:
    """The wait after an exit-5 bootstrap: "registered", then print hangs."""
    mock_run.side_effect = _registered_then_hung([5], clear_first=True)
    with pytest.raises(LaunchctlError, match="last seen still registered") as info:
        agent.bootstrap()
    assert "stopped answering before the 0.25s wait after exit 5 ended" in str(
        info.value
    )
