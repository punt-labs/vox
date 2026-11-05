"""Tests for :class:`PiRpcChannel`: every way the pipe can fail ends the agent."""

from __future__ import annotations

import asyncio
from typing import cast

import pytest

from conversation_mode._pi_rpc_fakes import FakePiProcess
from punt_vox.voxd.conversation_mode import pi_rpc_channel as channel_module
from punt_vox.voxd.conversation_mode.call_agent_errors import CallAgentEndedError
from punt_vox.voxd.conversation_mode.pi_rpc_channel import PiRpcChannel


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
