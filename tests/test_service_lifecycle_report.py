"""Tests for punt_vox.service.lifecycle_report -- the closed outcome table."""

from __future__ import annotations

import pytest

from punt_vox.service.lifecycle_report import LifecycleOutcome, LifecycleReport
from punt_vox.types_health import HealthStatus

# Design section 4.4, verbatim: the five successes; every other outcome fails.
_OK = {"stopped", "already_stopped", "started", "already_running", "restarted"}
_ALL = _OK | {
    "not_installed",
    "permission_denied",
    "backend_failed",
    "timeout",
    "port_conflict",
    "version_mismatch",
}


def test_outcome_set_is_closed() -> None:
    assert {o.value for o in LifecycleOutcome} == _ALL


@pytest.mark.parametrize("outcome", list(LifecycleOutcome))
def test_outcome_ok_and_exit_match_the_table(outcome: LifecycleOutcome) -> None:
    assert outcome.ok is (outcome.value in _OK)
    assert outcome.exit_code == (0 if outcome.value in _OK else 1)


def _report(outcome: LifecycleOutcome) -> LifecycleReport:
    return LifecycleReport(
        verb="stop", outcome=outcome, port=8421, log_path="/l/vox.log"
    )


def test_to_json_shape() -> None:
    report = LifecycleReport(
        verb="stop",
        outcome=LifecycleOutcome.STOPPED,
        port=8421,
        log_path="/l/vox.log",
        detail="voxd.service stopped; port 8421 free",
        previous_pid=1471111,
    )
    assert report.to_json() == {
        "verb": "stop",
        "outcome": "stopped",
        "ok": True,
        "pid": None,
        "previous_pid": 1471111,
        "port": 8421,
        "daemon_version": None,
        "detail": "voxd.service stopped; port 8421 free",
        "log_path": "/l/vox.log",
    }
    assert report.exit_code == 0


def test_text_failure_points_at_the_log() -> None:
    report = _report(LifecycleOutcome.TIMEOUT)
    assert report.text().splitlines() == [
        "vox daemon stop failed: timeout",
        "  Check the logs at /l/vox.log",
    ]


def test_as_verb_prefixes_the_failed_half() -> None:
    stop = LifecycleReport(
        verb="stop",
        outcome=LifecycleOutcome.TIMEOUT,
        port=8421,
        log_path="/l",
        detail="still active",
    )
    relabelled = stop.as_verb("restart")
    assert (relabelled.verb, relabelled.detail) == ("restart", "stop: still active")


def test_restarted_after_names_both_processes() -> None:
    stop = LifecycleReport(
        verb="stop",
        outcome=LifecycleOutcome.STOPPED,
        port=8421,
        log_path="/l",
        previous_pid=100,
    )
    start = LifecycleReport(
        verb="start", outcome=LifecycleOutcome.STARTED, port=8421, log_path="/l"
    ).serving(HealthStatus(pid=500, daemon_version="5.0.6"))
    report = start.restarted_after(stop)
    assert (report.verb, report.outcome) == ("restart", LifecycleOutcome.RESTARTED)
    assert (report.pid, report.previous_pid, report.daemon_version) == (
        500,
        100,
        "5.0.6",
    )
    assert (
        report.text()
        == "voxd restarted (pid 500, port 8421, version 5.0.6) (was pid 100)"
    )
