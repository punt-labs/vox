"""Drive the fake Conversational AI agent with a raw websockets client."""

from __future__ import annotations

import asyncio
import base64
import json
from typing import TYPE_CHECKING, cast

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import ServerConnection
from websockets.exceptions import ConnectionClosed, ConnectionClosedOK

from .fake_agent import FakeConvAIAgent
from .steps import (
    ExpectAudioChunks,
    ExpectPong,
    HangUp,
    SendAgentResponse,
    SendAudio,
    SendInterruption,
    SendPing,
    SendToolCall,
    SendUserTranscript,
    WaitForLogLine,
    WaitForToolResult,
)

if TYPE_CHECKING:
    from pathlib import Path

    from websockets.asyncio.client import ClientConnection

    from .steps import Step

type Message = dict[str, object]

_INITIATION = {
    "type": "conversation_initiation_client_data",
    "conversation_config_override": {
        "agent": {
            "prompt": {
                "prompt": "be brief",
                "tool_ids": ["t1"],
                "tool_ids_strict": True,
            },
            "first_message": "hello there",
        },
        "conversation": {"text_only": False},
    },
}


async def _recv(socket: ClientConnection) -> Message:
    return cast("Message", json.loads(await socket.recv()))


async def _open(agent: FakeConvAIAgent) -> ClientConnection:
    socket = await connect(agent.signed_url)
    await socket.send(json.dumps(_INITIATION))
    return socket


async def _run(steps: tuple[Step, ...], text: str) -> FakeConvAIAgent:
    """Connect, read one frame after metadata, and return the finished agent."""
    async with asyncio.timeout(2), FakeConvAIAgent(steps) as agent:
        socket = await _open(agent)
        await _recv(socket)
        frame = await _recv(socket)
        assert frame["type"] == text
        await socket.close()
    return agent


async def test_initiation_is_captured() -> None:
    async with asyncio.timeout(2), FakeConvAIAgent([HangUp()]) as agent:
        socket = await _open(agent)
        await _recv(socket)
        await socket.wait_closed()
    override = cast("Message", agent.initiation["conversation_config_override"])
    agent_override = cast("Message", override["agent"])
    assert agent_override["first_message"] == "hello there"


async def test_metadata_is_sent_first() -> None:
    async with asyncio.timeout(2), FakeConvAIAgent([HangUp()]) as agent:
        socket = await _open(agent)
        metadata = await _recv(socket)
    event = cast("Message", metadata["conversation_initiation_metadata_event"])
    assert (metadata["type"], event["agent_output_audio_format"]) == (
        "conversation_initiation_metadata",
        "pcm_16000",
    )


async def test_tool_call_round_trip() -> None:
    steps: tuple[Step, ...] = (
        SendToolCall("start_worker", "call_1", {"task": "add greeting.py"}),
        WaitForToolResult("call_1"),
        HangUp(),
    )
    async with asyncio.timeout(2), FakeConvAIAgent(steps) as agent:
        socket = await _open(agent)
        await _recv(socket)
        call = cast("Message", (await _recv(socket))["client_tool_call"])
        assert call["tool_name"] == "start_worker"
        result = {
            "type": "client_tool_result",
            "tool_call_id": "call_1",
            "result": "started w1",
            "is_error": False,
        }
        await socket.send(json.dumps(result))
        await socket.wait_closed()
    assert agent.tool_results["call_1"]["result"] == "started w1"


async def test_audio_chunks_are_counted() -> None:
    steps: tuple[Step, ...] = (ExpectAudioChunks(3), HangUp())
    chunk = json.dumps({"user_audio_chunk": base64.b64encode(b"\x00\x01" * 8).decode()})
    async with asyncio.timeout(2), FakeConvAIAgent(steps) as agent:
        socket = await _open(agent)
        await _recv(socket)
        for _ in range(3):
            await socket.send(chunk)
        await socket.wait_closed()
    assert agent.audio_chunks == 3


async def test_hang_up_closes_the_socket() -> None:
    async with asyncio.timeout(2), FakeConvAIAgent([HangUp()]) as agent:
        socket = await _open(agent)
        await _recv(socket)
        with pytest.raises(ConnectionClosed):
            await socket.recv()


async def test_server_ping_is_the_real_shape_and_the_pong_is_awaited() -> None:
    """The fake pings as ElevenLabs does and waits for the client's pong."""
    steps: tuple[Step, ...] = (SendPing(7), ExpectPong(7), HangUp())
    async with asyncio.timeout(2), FakeConvAIAgent(steps) as agent:
        socket = await _open(agent)
        await _recv(socket)
        ping = await _recv(socket)
        assert ping == {"type": "ping", "ping_event": {"event_id": 7, "ping_ms": 0}}
        await socket.send(json.dumps({"type": "pong", "event_id": 7}))
        await socket.wait_closed()
    assert agent.pongs == {7}
    assert agent.cut_short is None


@pytest.mark.parametrize(
    ("step", "frame_type"),
    [
        (SendAudio(b"\x01\x00" * 4, 1), "audio"),
        (SendUserTranscript("hi"), "user_transcript"),
        (SendAgentResponse("hello", 2), "agent_response"),
        (SendInterruption(3), "interruption"),
    ],
)
async def test_each_send_step_emits_its_frame(step: Step, frame_type: str) -> None:
    await _run((step, HangUp()), frame_type)


async def test_client_close_ends_the_script_without_raising() -> None:
    steps: tuple[Step, ...] = (WaitForToolResult("never"), SendAudio(b"\x00", 1))
    async with asyncio.timeout(2), FakeConvAIAgent(steps) as agent:
        socket = await _open(agent)
        await _recv(socket)
        await socket.close()
    assert agent.tool_results == {}


async def test_undecodable_client_frame_surfaces_as_a_fault() -> None:
    """A frame the fake cannot decode fails the test at once, not at a timeout."""
    agent = FakeConvAIAgent((WaitForToolResult("never"), HangUp()))
    with pytest.raises(json.JSONDecodeError):
        async with asyncio.timeout(1), agent:
            socket = await _open(agent)
            await _recv(socket)
            await socket.send("not json")
            with pytest.raises(ConnectionClosed) as closed:
                await socket.recv()
            assert closed.value.rcvd is not None
            assert closed.value.rcvd.code == 1011
    assert isinstance(agent.fault, json.JSONDecodeError)


async def test_player_error_surfaces_as_a_fault() -> None:
    """A step the fake cannot play fails the test with the real error."""
    agent = FakeConvAIAgent((SendAudio(cast("bytes", "not bytes"), 1),))
    with pytest.raises(TypeError):
        async with asyncio.timeout(1), agent:
            socket = await _open(agent)
            await _recv(socket)
            with pytest.raises(ConnectionClosed) as closed:
                await socket.recv()
            assert closed.value.rcvd is not None
            assert closed.value.rcvd.code == 1011
    assert isinstance(agent.fault, TypeError)


async def test_client_close_during_a_wait_is_recorded_as_cut_short() -> None:
    """A wait step the client's close ends is not a success; the fake says which."""
    steps: tuple[Step, ...] = (WaitForToolResult("never"), HangUp())
    async with asyncio.timeout(2), FakeConvAIAgent(steps) as agent:
        socket = await _open(agent)
        await _recv(socket)
        await socket.close()
    assert agent.cut_short == WaitForToolResult("never")


async def test_client_close_during_a_send_step_is_recorded_as_cut_short(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A send the client's close lands on is not a success; the fake says which.

    The close frame racing a send is not reproducible on demand, so the
    server's send stands in for it: it raises ConnectionClosed, as the real
    send does once the client's close has been read.
    """
    real_send = ServerConnection.send

    async def closed_on_audio(self: ServerConnection, message: str) -> None:
        if '"type": "audio"' in message:
            raise ConnectionClosedOK(None, None)
        await real_send(self, message)

    monkeypatch.setattr(ServerConnection, "send", closed_on_audio)
    steps: tuple[Step, ...] = (
        SendUserTranscript("hi"),
        SendAudio(b"\x00", 1),
        SendAgentResponse("never sent", 2),
        HangUp(),
    )
    async with asyncio.timeout(2), FakeConvAIAgent(steps) as agent:
        socket = await _open(agent)
        await _recv(socket)
        assert (await _recv(socket))["type"] == "user_transcript"
        await socket.close()
    assert agent.fault is None
    assert agent.cut_short == SendAudio(b"\x00", 1)


async def test_a_completed_script_is_not_cut_short() -> None:
    steps: tuple[Step, ...] = (ExpectAudioChunks(1), HangUp())
    chunk = json.dumps({"user_audio_chunk": base64.b64encode(b"\x00\x01").decode()})
    async with asyncio.timeout(2), FakeConvAIAgent(steps) as agent:
        socket = await _open(agent)
        await _recv(socket)
        await socket.send(chunk)
        await socket.wait_closed()
    assert agent.cut_short is None


async def test_a_first_message_that_is_not_the_initiation_is_a_fault() -> None:
    """The fake refuses a client that skips conversation_initiation_client_data."""
    agent = FakeConvAIAgent([HangUp()])
    with pytest.raises(ValueError, match="conversation_initiation_client_data"):
        async with asyncio.timeout(1), agent:
            socket = await connect(agent.signed_url)
            await socket.send(json.dumps({"type": "pong", "event_id": 1}))
            with pytest.raises(ConnectionClosed) as closed:
                await socket.recv()
            assert closed.value.rcvd is not None
            assert closed.value.rcvd.code == 1011
    assert isinstance(agent.fault, ValueError)
    assert agent.initiation == {}


async def test_a_second_connection_is_refused_as_a_fault() -> None:
    """One script serves one conversation; a second dial is a daemon bug."""
    steps: tuple[Step, ...] = (WaitForToolResult("never"), HangUp())
    agent = FakeConvAIAgent(steps)
    with pytest.raises(RuntimeError, match="second connection"):
        async with asyncio.timeout(1), agent:
            first = await _open(agent)
            await _recv(first)
            second = await connect(agent.signed_url)
            with pytest.raises(ConnectionClosed) as closed:
                await second.recv()
            assert closed.value.rcvd is not None
            assert closed.value.rcvd.code == 1011
            await first.close()
    assert isinstance(agent.fault, RuntimeError)


async def test_a_log_line_wait_proceeds_once_the_line_is_written(
    tmp_path: Path,
) -> None:
    """The step blocks until the daemon's log carries the text, then plays on."""
    log = tmp_path / "vox.log"
    steps: tuple[Step, ...] = (
        WaitForLogLine(log, "PCM playback started"),
        SendInterruption(1),
        HangUp(),
    )
    async with asyncio.timeout(2), FakeConvAIAgent(steps) as agent:
        socket = await _open(agent)
        await _recv(socket)
        await asyncio.sleep(0.15)
        log.write_text("PCM stream open in 3 ms\nPCM playback started at 25 ms\n")
        assert (await _recv(socket))["type"] == "interruption"
        await socket.wait_closed()
    assert agent.cut_short is None


async def test_a_log_line_that_never_comes_is_a_fault_naming_it(
    tmp_path: Path,
) -> None:
    """Past its timeout the wait fails the test with the missing text."""
    log = tmp_path / "vox.log"
    agent = FakeConvAIAgent((WaitForLogLine(log, "never written", 0.2), HangUp()))
    with pytest.raises(TimeoutError, match="'never written' within 0s"):
        async with asyncio.timeout(2), agent:
            socket = await _open(agent)
            await _recv(socket)
            with pytest.raises(ConnectionClosed) as closed:
                await socket.recv()
            assert closed.value.rcvd is not None
            assert closed.value.rcvd.code == 1011
    assert isinstance(agent.fault, TimeoutError)


async def test_a_client_close_during_a_log_line_wait_is_cut_short(
    tmp_path: Path,
) -> None:
    steps: tuple[Step, ...] = (WaitForLogLine(tmp_path / "vox.log", "x"), HangUp())
    async with asyncio.timeout(2), FakeConvAIAgent(steps) as agent:
        socket = await _open(agent)
        await _recv(socket)
        await socket.close()
        await asyncio.sleep(0.1)
    assert agent.cut_short == WaitForLogLine(tmp_path / "vox.log", "x")
