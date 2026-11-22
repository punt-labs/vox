"""Keep CLI status outcome records singular and free of endpoint or health data."""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from punt_vox.__main__ import app
from punt_vox.client_errors import VoxdProtocolError, VoxdRefusedError
from punt_vox.service.daemon_status import DaemonStatus
from punt_vox.types_health import HealthStatus


@pytest.mark.parametrize("command", [["status"], ["daemon", "status"]])
@pytest.mark.parametrize("flags", [[], ["--json"], ["--quiet"]])
@pytest.mark.parametrize("outcome", ["running", "not_running", "unknown"])
def test_status_outcome(
    command: list[str],
    flags: list[str],
    outcome: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("VOXD_HOST", "PRIVATE_HOST")
    monkeypatch.setenv("VOXD_PORT", "54321")
    health = HealthStatus(status="ok", port=54321, daemon_version="PRIVATE_PAYLOAD")
    failure = (
        VoxdRefusedError("PRIVATE_EXCEPTION")
        if outcome == "not_running"
        else VoxdProtocolError("PRIVATE_EXCEPTION")
    )
    target = "punt_vox.cli_daemon" if command[0] == "daemon" else "punt_vox.__main__"
    with (
        patch("punt_vox.cli_io.configure_client_logging"),
        patch(f"{target}.VoxClientSync") as client,
        caplog.at_level(logging.INFO),
    ):
        client.return_value.health.return_value = health
        if outcome != "running":
            client.return_value.health.side_effect = failure
        result = CliRunner().invoke(app, [*command, *flags])
    daemon = command[0] == "daemon"
    assert result.exit_code == (1 if daemon and outcome != "running" else 0)
    expected = outcome if daemon or outcome == "running" else "not_running"
    reason = {
        "running": "reachable",
        "not_running": "unreachable",
        "unknown": "indeterminate",
    }[expected]
    records = [r for r in caplog.records if r.name == "punt_vox.cli_daemon"]
    assert [(r.levelno, r.getMessage()) for r in records] == [
        (logging.INFO, f"vox {' '.join(command)}: outcome={expected} reason={reason}")
    ]
    assert "PRIVATE" not in caplog.text
    assert "54321" not in caplog.text
    if not flags:
        label = "ok" if daemon and outcome == "running" else expected.replace("_", " ")
        assert f"Daemon: {'   ' if not daemon else ''}{label}" in result.output
    if flags == ["--quiet"]:
        assert result.output == ""


def test_local_unresponsive_status(caplog: pytest.LogCaptureFixture) -> None:
    with (
        patch("punt_vox.cli_io.configure_client_logging"),
        patch(
            "punt_vox.cli_daemon.DaemonCli._read_status",
            return_value=DaemonStatus.unresponsive([54321], "PRIVATE_EXCEPTION"),
        ),
        caplog.at_level(logging.INFO),
    ):
        result = CliRunner().invoke(app, ["daemon", "status", "--json"])
    assert result.exit_code == 1
    assert [
        r.getMessage() for r in caplog.records if r.name == "punt_vox.cli_daemon"
    ] == ["vox daemon status: outcome=unresponsive reason=unresponsive"]
    assert "PRIVATE" not in caplog.text
    assert "54321" not in caplog.text
