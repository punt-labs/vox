"""Drive the fake Conversational AI agent with a raw websockets client."""

from __future__ import annotations

import asyncio
import base64
import json
from typing import TYPE_CHECKING, cast

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from .fake_agent import FakeConvAIAgent
from .steps import (
    ExpectAudioChunks,
    HangUp,
    SendAgentResponse,
    SendAudio,
    SendInterruption,
    SendToolCall,
    SendUserTranscript,
    WaitForToolResult,
)

if TYPE_CHECKING:
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


async def test_ping_is_answered_with_pong() -> None:
    steps: tuple[Step, ...] = (ExpectAudioChunks(1), HangUp())
    async with asyncio.timeout(2), FakeConvAIAgent(steps) as agent:
        socket = await _open(agent)
        await _recv(socket)
        await socket.send(json.dumps({"type": "ping", "ping_event": {}, "event_id": 7}))
        pong = await _recv(socket)
        await socket.send(json.dumps({"user_audio_chunk": ""}))
        await socket.wait_closed()
    assert pong == {"type": "pong", "event_id": 7}
    assert [m["type"] for m in agent.received if "type" in m] == ["ping"]


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
