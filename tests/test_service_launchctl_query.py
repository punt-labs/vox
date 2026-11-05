"""Tests for punt_vox.service.launchctl_query -- bounded launchctl queries.

The agent-level behaviour (bootout, bootstrap, the unregister wait) is in
test_service_launchctl.py; these pin the query object's own contract.
"""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from punt_vox.service import launchctl
from punt_vox.service.launchctl_query import LaunchctlError, LaunchctlQuery

_LABEL = "com.punt-labs.voxd"
_TARGET = f"gui/501/{_LABEL}"


def _query() -> LaunchctlQuery:
    return LaunchctlQuery(_LABEL, _TARGET)


def _completed(code: int, stdout: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["launchctl"], code, stdout, "err")


@pytest.mark.parametrize(("code", "gone"), [(0, False), (113, True)])
@patch("punt_vox.service.launchctl_query.subprocess.run")
def test_left_within_reads_113_as_gone(
    mock_run: MagicMock, code: int, gone: bool
) -> None:
    mock_run.return_value = _completed(code)
    answer = _query().left_within(0.5)
    assert answer.holds is gone
    assert answer.timed_out is False
    assert mock_run.call_args.args[0] == ["launchctl", "print", _TARGET]
    assert mock_run.call_args.kwargs["timeout"] == 0.5


@patch(
    "punt_vox.service.launchctl_query.subprocess.run",
    side_effect=subprocess.TimeoutExpired(cmd="launchctl", timeout=0.5),
)
def test_left_within_cut_short_names_the_print(_mock_run: MagicMock) -> None:
    answer = _query().left_within(0.5)
    assert answer.holds is False
    assert answer.unanswered == f"launchctl print {_TARGET}"


@patch("punt_vox.service.launchctl_query.subprocess.run")
def test_left_within_failed_print_raises(mock_run: MagicMock) -> None:
    mock_run.return_value = _completed(1)
    with pytest.raises(LaunchctlError, match="exit 1"):
        _query().left_within(0.5)


@patch(
    "punt_vox.service.launchctl_query.subprocess.run",
    side_effect=FileNotFoundError("launchctl"),
)
def test_a_launchctl_that_cannot_start_raises(_mock_run: MagicMock) -> None:
    with pytest.raises(LaunchctlError, match="could not run"):
        _query().left_within(0.5)


def test_launchctl_error_has_one_home() -> None:
    """Callers import LaunchctlError from launchctl_query; no second name."""
    assert "LaunchctlError" not in launchctl.__all__
