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

from punt_vox.service.launchctl import LaunchctlAgent, LaunchctlError

if TYPE_CHECKING:
    from collections.abc import Callable

_PLIST = "/var/tmp/com.punt-labs.voxd.plist"  # test literal path, never written
_LABEL = "com.punt-labs.voxd"


def _completed(returncode: int, stderr: str = "") -> subprocess.CompletedProcess[str]:
    """Build a CompletedProcess with the given exit code."""
    return subprocess.CompletedProcess(
        args=["launchctl"], returncode=returncode, stdout="", stderr=stderr
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


@patch("punt_vox.service.launchctl.subprocess.run")
def test_is_registered_true_when_print_exits_zero(
    mock_run: MagicMock, agent: LaunchctlAgent
) -> None:
    """launchctl print exit 0 means the job is still registered."""
    mock_run.return_value = _completed(0)
    assert agent.is_registered() is True


@patch("punt_vox.service.launchctl.subprocess.run")
def test_is_registered_false_when_print_nonzero(
    mock_run: MagicMock, agent: LaunchctlAgent
) -> None:
    """launchctl print non-zero means the job has left the domain."""
    mock_run.return_value = _completed(1)
    assert agent.is_registered() is False


@patch("punt_vox.service.launchctl.subprocess.run")
def test_wait_returns_immediately_when_already_unregistered(
    mock_run: MagicMock, agent: LaunchctlAgent
) -> None:
    """A clear domain returns True on the first poll without sleeping."""
    mock_run.return_value = _completed(1)  # not registered
    with patch("punt_vox.service.launchctl.time.sleep") as mock_sleep:
        assert agent.wait_until_unregistered() is True
    mock_sleep.assert_not_called()


@patch("punt_vox.service.launchctl.subprocess.run")
def test_wait_times_out_when_job_never_clears(
    mock_run: MagicMock, agent: LaunchctlAgent, fast_bound: None
) -> None:
    """A job that stays registered makes the bounded wait report failure."""
    mock_run.return_value = _completed(0)  # always registered
    assert agent.wait_until_unregistered() is False


# ---------------------------------------------------------------------------
# bootout -- boots out, then waits for the domain to clear
# ---------------------------------------------------------------------------


@patch("punt_vox.service.launchctl.subprocess.run")
def test_bootout_waits_for_unregister(
    mock_run: MagicMock, agent: LaunchctlAgent
) -> None:
    """bootout issues the bootout then confirms the job left the domain."""
    mock_run.side_effect = _dispatch(print_rc=1, bootstrap_rcs=[])
    agent.bootout()
    verbs = [_verb(c) for c in mock_run.call_args_list]
    assert verbs[0] == "bootout"
    assert "print" in verbs  # the wait polled registration
    # bootout targets the GUI domain and never shells out to sudo.
    for call in mock_run.call_args_list:
        assert call[0][0][0] == "launchctl"


# ---------------------------------------------------------------------------
# bootstrap -- happy, retry-on-5, and never-unregister failure
# ---------------------------------------------------------------------------


@patch("punt_vox.service.launchctl.subprocess.run")
def test_bootstrap_happy_path(mock_run: MagicMock, agent: LaunchctlAgent) -> None:
    """Clean domain, bootstrap succeeds first try -- no error, one bootstrap."""
    mock_run.side_effect = _dispatch(print_rc=1, bootstrap_rcs=[0])
    agent.bootstrap()
    verbs = [_verb(c) for c in mock_run.call_args_list]
    assert verbs.count("bootstrap") == 1
    # bootstrap targets gui/<uid>, never sudo.
    bootstrap_call = next(c for c in mock_run.call_args_list if _verb(c) == "bootstrap")
    assert bootstrap_call[0][0][2].startswith("gui/")


@patch("punt_vox.service.launchctl.subprocess.run")
def test_bootstrap_retries_once_on_exit_5(
    mock_run: MagicMock, agent: LaunchctlAgent
) -> None:
    """Exit 5 (stale registration) then success after the job clears."""
    mock_run.side_effect = _dispatch(print_rc=1, bootstrap_rcs=[5, 0])
    agent.bootstrap()  # must not raise
    verbs = [_verb(c) for c in mock_run.call_args_list]
    assert verbs.count("bootstrap") == 2  # first hit 5, retry succeeded


@patch("punt_vox.service.launchctl.subprocess.run")
def test_bootstrap_raises_when_job_never_unregisters(
    mock_run: MagicMock, agent: LaunchctlAgent, fast_bound: None
) -> None:
    """Case (c): the job never leaves the domain -- bootstrap reports failure."""
    mock_run.return_value = _completed(0)  # print always says registered
    with pytest.raises(LaunchctlError, match="did not leave"):
        agent.bootstrap()


@patch("punt_vox.service.launchctl.subprocess.run")
def test_bootstrap_raises_when_exit_5_persists(
    mock_run: MagicMock, agent: LaunchctlAgent
) -> None:
    """A non-transient bootstrap failure surfaces as LaunchctlError."""
    mock_run.side_effect = _dispatch(print_rc=1, bootstrap_rcs=[5, 5])
    with pytest.raises(LaunchctlError, match="bootstrap"):
        agent.bootstrap()


@patch("punt_vox.service.launchctl.subprocess.run")
def test_bootstrap_raises_on_non_io_error(
    mock_run: MagicMock, agent: LaunchctlAgent
) -> None:
    """A non-5 bootstrap exit is not retried and raises immediately."""
    mock_run.side_effect = _dispatch(print_rc=1, bootstrap_rcs=[9])
    with pytest.raises(LaunchctlError, match="exit 9"):
        agent.bootstrap()
    verbs = [_verb(c) for c in mock_run.call_args_list]
    assert verbs.count("bootstrap") == 1  # no retry on a non-5 code


# ---------------------------------------------------------------------------
# start / kickstart
# ---------------------------------------------------------------------------


@patch("punt_vox.service.launchctl.subprocess.run")
def test_start_bootstraps_then_kickstarts(
    mock_run: MagicMock, agent: LaunchctlAgent
) -> None:
    """start brings the job up: bootstrap then kickstart, both succeeding."""
    mock_run.side_effect = _dispatch(print_rc=1, bootstrap_rcs=[0], kickstart_rc=0)
    agent.start()
    verbs = [_verb(c) for c in mock_run.call_args_list]
    assert verbs.count("bootstrap") == 1
    assert verbs.count("kickstart") == 1
    assert verbs.index("bootstrap") < verbs.index("kickstart")


@patch("punt_vox.service.launchctl.subprocess.run")
def test_kickstart_failure_raises(mock_run: MagicMock, agent: LaunchctlAgent) -> None:
    """A failed kickstart surfaces as LaunchctlError so start() reports it."""
    mock_run.side_effect = _dispatch(print_rc=1, bootstrap_rcs=[0], kickstart_rc=1)
    with pytest.raises(LaunchctlError, match="kickstart"):
        agent.start()
