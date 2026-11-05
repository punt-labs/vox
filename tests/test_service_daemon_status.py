"""``DaemonStatus``: the closed set of ``vox daemon status`` answers and their shape."""

from __future__ import annotations

from punt_vox.service.daemon_status import DaemonState, DaemonStatus
from punt_vox.types_health import HealthStatus


def test_running_reports_the_health_snapshot_and_exits_zero() -> None:
    health = HealthStatus(status="ok", port=8421, pid=42, daemon_version="5.0.6")
    reading = DaemonStatus.running(health)
    assert reading.state is DaemonState.RUNNING
    assert reading.exit_code == 0
    payload = reading.to_json()
    assert payload["status"] == "ok"
    assert payload["pid"] == 42
    assert "reason" not in payload
    assert reading.text().startswith("Daemon: ok on port 8421")


def test_unresponsive_names_the_sorted_pids_and_the_cause() -> None:
    reading = DaemonStatus.unresponsive([300, 100], "no answer from voxd within 5.0s")
    assert reading.exit_code == 1
    assert reading.to_json() == {
        "status": "unresponsive",
        "reason": "voxd pid 100, 300 holds the port but did not answer health: "
        "no answer from voxd within 5.0s",
        "pids": [100, 300],
    }
    assert reading.text().startswith("Daemon: unresponsive (voxd pid 100, 300")


def test_unknown_carries_only_its_reason() -> None:
    reading = DaemonStatus.unknown("cannot run fuser: not found")
    assert reading.exit_code == 1
    assert reading.to_json() == {
        "status": "unknown",
        "reason": "cannot run fuser: not found",
    }


def test_not_running_without_an_off_port_voxd_has_no_off_port_key() -> None:
    reading = DaemonStatus.not_running("no voxd")
    assert reading.to_json() == {"status": "not_running", "reason": "no voxd"}
    assert reading.text() == "Daemon: not running (no voxd)"


def test_not_running_names_an_off_port_voxd_in_reason_and_json() -> None:
    reading = DaemonStatus.not_running("no voxd", [9, 7])
    payload = reading.to_json()
    assert payload["off_port_pids"] == [7, 9]
    assert payload["reason"] == "no voxd; voxd pid 7, 9 on another port was not counted"
