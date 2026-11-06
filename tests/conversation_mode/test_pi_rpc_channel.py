"""Tests for :class:`PiRpcChannel`: every way the pipe can fail ends the agent."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest

from conversation_mode._pi_rpc_fakes import FakePiProcess
from punt_vox.voxd.conversation_mode import pi_rpc_channel as channel_module
from punt_vox.voxd.conversation_mode.call_agent_errors import (
    CallAgentEndedError,
    CallAgentSpawnError,
)
from punt_vox.voxd.conversation_mode.call_agent_launch import CallAgentLaunch
from punt_vox.voxd.conversation_mode.pi_rpc_channel import PiRpcChannel
from punt_vox.voxd.conversation_mode.secret_path_policy import SecretPathPolicy

_EXEC = "punt_vox.voxd.conversation_mode.pi_rpc_channel.asyncio.create_subprocess_exec"


def _channel(process: FakePiProcess) -> PiRpcChannel:
    # FakePiProcess duck-types the Process surface the channel uses.
    return PiRpcChannel(cast("asyncio.subprocess.Process", process))


async def test_a_non_json_line_ends_the_agent() -> None:
    process = FakePiProcess([])
    process.stdout.feed_data(b"Warning: something odd\n")
    with pytest.raises(CallAgentEndedError, match="malformed"):
        await _channel(process).receive(1.0)


async def test_silence_past_the_bound_ends_the_agent() -> None:
    with pytest.raises(CallAgentEndedError, match="no event"):
        await _channel(FakePiProcess([])).receive(0.01)


async def test_stdout_eof_names_the_stderr_reason() -> None:
    process = FakePiProcess([])
    channel = _channel(process)
    process.stderr.feed_data(b"auth failed\n")
    process.exit(1)
    with pytest.raises(CallAgentEndedError, match="auth failed"):
        await channel.receive(1.0)


async def test_sending_to_an_exited_process_ends_the_agent() -> None:
    process = FakePiProcess([])
    channel = _channel(process)
    process.exit(1)
    with pytest.raises(CallAgentEndedError):
        await channel.send({"type": "prompt", "message": "hi"})


async def test_close_kills_a_process_that_will_not_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = FakePiProcess([], exits_on_stdin_close=False)
    monkeypatch.setattr(channel_module, "_SHUTDOWN_GRACE_S", 0.01)
    await _channel(process).close()
    assert process.killed


async def test_stdout_eof_names_the_exit_code() -> None:
    process = FakePiProcess([])
    channel = _channel(process)
    process.exit(3)
    with pytest.raises(CallAgentEndedError, match="exit 3"):
        await channel.receive(1.0)


async def test_a_dead_process_on_send_names_its_stderr_reason() -> None:
    process = FakePiProcess([])
    channel = _channel(process)
    process.stderr.feed_data(b"Failed to load extension\n")
    process.exit(1)
    with pytest.raises(CallAgentEndedError, match="Failed to load extension"):
        await channel.send({"type": "prompt", "message": "hi"})


@pytest.mark.parametrize(
    "error", [OSError(5, "I/O error"), BrokenPipeError(), ConnectionResetError()]
)
async def test_any_write_failure_ends_the_agent(error: OSError) -> None:
    process = FakePiProcess([])
    channel = _channel(process)
    with (
        patch.object(type(process.stdin), "write", side_effect=error),
        pytest.raises(CallAgentEndedError),
    ):
        await channel.send({"type": "prompt", "message": "hi"})


@pytest.mark.parametrize(
    ("error", "named"),
    [
        (FileNotFoundError(2, "No such file"), "not found on PATH"),
        (PermissionError(13, "Permission denied"), "Permission denied"),
        (OSError(8, "Exec format error"), "Exec format error"),
    ],
)
async def test_any_spawn_failure_is_a_spawn_error_naming_the_cause(
    tmp_path: Path, error: OSError, named: str
) -> None:
    launch = CallAgentLaunch(SecretPathPolicy(tmp_path))
    with (
        patch(_EXEC, side_effect=error),
        pytest.raises(CallAgentSpawnError, match=named) as caught,
    ):
        await PiRpcChannel.open(launch, tmp_path)
    assert launch.program in str(caught.value)


async def test_close_logs_a_non_zero_exit_with_the_stderr_tail(
    caplog: pytest.LogCaptureFixture,
) -> None:
    process = FakePiProcess([], exits_on_stdin_close=False)
    channel = _channel(process)
    process.stderr.feed_data(b"shutdown hook failed\n")
    with caplog.at_level(logging.WARNING, logger=channel_module.__name__):
        process.exit(2)
        await channel.close()
    assert "exit 2" in caplog.text
    assert "shutdown hook failed" in caplog.text


async def test_a_cancellation_during_the_close_grace_kills_pi(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = FakePiProcess([], exits_on_stdin_close=False)
    monkeypatch.setattr(channel_module, "_SHUTDOWN_GRACE_S", 30.0)
    closing = asyncio.ensure_future(_channel(process).close())
    await asyncio.sleep(0.01)
    closing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await closing
    assert process.killed
