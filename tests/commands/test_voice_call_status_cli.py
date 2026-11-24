"""CLI output for on-demand voice-call status."""

from __future__ import annotations

import json
from unittest.mock import ANY, patch

import pytest
from typer.testing import CliRunner

from punt_vox.__main__ import app
from punt_vox.cli_io import OutputFlags
from punt_vox.client_errors import VoxdConnectionError
from punt_vox.commands.call import CallCli
from punt_vox.daemon_control import DaemonControl
from punt_vox.output_formatter import OutputFormatter

runner = CliRunner()


def _ended_status() -> dict[str, object]:
    return {
        "state": "ended",
        "call_id": "first",
        "transcript": [{"role": "agent", "text": "Started a worker on demo."}],
        "tool_calls": [{"tool": "start_worker", "arguments": {"repo": "demo"}}],
        "trace": "/tmp/call-trace.jsonl",
    }


def test_call_status_uses_the_supplied_output_pair() -> None:
    formatter = OutputFormatter()
    flags = OutputFlags(formatter)
    flags.apply(json_output=True, verbose=False, quiet=False)
    with patch.object(DaemonControl, "request", side_effect=[_ended_status()]):
        result = runner.invoke(CallCli.build_app((formatter, flags)), ["status"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == _ended_status()


def test_standalone_call_status_applies_command_output_flags() -> None:
    with patch.object(DaemonControl, "request", side_effect=[_ended_status()]):
        result = runner.invoke(CallCli.build_app(), ["status", "--json"])

    assert result.exit_code == 0, result.output
    assert not result.stderr
    assert json.loads(result.stdout) == _ended_status()


@pytest.mark.parametrize("global_flag", [True, False])
@pytest.mark.parametrize("flag", ["--json", "--quiet"])
def test_call_status_output_flags(flag: str, *, global_flag: bool) -> None:
    command = ["call", "status"]
    args = [flag, *command] if global_flag else [*command, flag]
    with patch.object(
        DaemonControl, "request", side_effect=[_ended_status()]
    ) as request:
        result = runner.invoke(app, args)

    assert result.exit_code == 0, result.output
    request.assert_awaited_once_with("status", timeout=ANY)
    assert not result.stderr
    if flag == "--json":
        assert json.loads(result.stdout) == _ended_status()
    else:
        assert not result.stdout


@pytest.mark.parametrize("global_flag", [True, False])
@pytest.mark.parametrize("flag", ["--json", "--quiet"])
def test_call_status_error_output_flags(flag: str, *, global_flag: bool) -> None:
    command = ["call", "status"]
    args = [flag, *command] if global_flag else [*command, flag]
    with patch.object(
        DaemonControl, "request", side_effect=VoxdConnectionError("daemon is down")
    ):
        result = runner.invoke(app, args)

    assert result.exit_code == 1, result.output
    if flag == "--json":
        assert json.loads(result.stdout) == {"error": "daemon is down"}
        assert not result.stderr
    else:
        assert not result.stdout
        assert result.stderr == "Call unavailable: daemon is down\n"
