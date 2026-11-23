"""Keep CLI status outcome records singular and free of endpoint or health data."""

from __future__ import annotations

import json
import logging
import socket
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from punt_vox.__main__ import app
from punt_vox.client_errors import VoxdProtocolError, VoxdRefusedError
from punt_vox.paths import run_dir
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
    with (
        patch("punt_vox.cli_io.configure_client_logging"),
        patch("punt_vox.cli_daemon.VoxClientSync") as client,
        caplog.at_level(logging.INFO),
    ):
        client.return_value.health.return_value = health
        if outcome != "running":
            client.return_value.health.side_effect = failure
        result = CliRunner().invoke(app, [*command, *flags])
    daemon = command[0] == "daemon"
    assert result.exit_code == (1 if daemon and outcome != "running" else 0)
    expected = outcome
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


def _closed_port() -> int:
    """Return a localhost port that nothing listens on."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


@pytest.mark.parametrize(
    ("voxd_port", "serve_port", "expected"),
    [
        pytest.param(None, None, "not running", id="unpublished-serve-port"),
        pytest.param("closed", None, "not running", id="refused"),
        pytest.param("abc", None, "unknown", id="malformed-VOXD_PORT"),
        pytest.param(None, b"garbage", "unknown", id="non-numeric-serve-port"),
        pytest.param(None, b"\xff\xfe", "unknown", id="non-utf8-serve-port"),
    ],
)
def test_overview_reports_only_refusal_as_not_running(
    voxd_port: str | None,
    serve_port: bytes | None,
    expected: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("VOXD_HOST", "VOXD_PORT", "VOXD_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    if voxd_port is not None:
        port = str(_closed_port()) if voxd_port == "closed" else voxd_port
        monkeypatch.setenv("VOXD_PORT", port)
    if serve_port is not None:
        run_dir().mkdir(parents=True)
        (run_dir() / "serve.port").write_bytes(serve_port)
    with patch("punt_vox.cli_io.configure_client_logging"):
        text = CliRunner().invoke(app, ["status"])
        payload = CliRunner().invoke(app, ["status", "--json"])
    assert text.exit_code == 0
    assert f"Daemon:    {expected}\n" in text.output
    assert json.loads(payload.output)["daemon"] == expected
