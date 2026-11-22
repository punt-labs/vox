from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
from typing import TYPE_CHECKING

import pytest
from websockets.asyncio.client import ClientConnection
from websockets.asyncio.server import serve

from punt_vox.voxd.voice_agent import (
    ConvAIError,
    Conversation,
    EventTrace,
    SessionOptions,
)
from punt_vox.voxd.voice_agent._socket import MAX_MESSAGE_BYTES
from punt_vox.voxd.voice_agent.events import (
    AgentResponse,
    AgentResponseCorrection,
    AgentToolResponse,
    Audio,
    ClientToolCall,
    Interruption,
    Metadata,
    UserTranscript,
)

if TYPE_CHECKING:
    from websockets.asyncio.server import ServerConnection

_OPTIONS = SessionOptions("private prompt", ("tool_clock",), text_only=True)
_METADATA = {
    "type": "conversation_initiation_metadata",
    "conversation_initiation_metadata_event": {
        "conversation_id": "conv_test",
        "agent_output_audio_format": "pcm_16000",
        "user_input_audio_format": "pcm_16000",
    },
}


def _tool(call_id: str, *, expects_response: bool = True) -> str:
    return json.dumps(
        {
            "type": "client_tool_call",
            "client_tool_call": {
                "tool_call_id": call_id,
                "tool_name": "clock",
                "parameters": {"user_secret": "private argument"},
                "expects_response": expects_response,
            },
        }
    )


async def test_localhost_session_pairs_tool_ids_and_pongs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    observed: list[object] = []
    closed = asyncio.Event()

    async def peer(socket: ServerConnection) -> None:
        try:
            observed.append(json.loads(await socket.recv()))
            await socket.send('{"type":"ping","ping_event":{"event_id":12}}')
            observed.append(json.loads(await socket.recv()))
            await socket.send(json.dumps(_METADATA))
            observed.append(json.loads(await socket.recv()))
            await socket.send('{"type":"ignored_extension","data":"private extension"}')
            await socket.send(_tool("call_a"))
            await socket.send(_tool("call_a"))
            await socket.send(_tool("call_b"))
            await socket.send(_tool("call_no_reply", expects_response=False))
            await socket.send('{"type":"ping","ping_event":{"event_id":13}}')
            observed.extend([json.loads(await socket.recv()) for _ in range(3)])
            await socket.send(
                json.dumps(
                    {
                        "type": "agent_tool_response",
                        "agent_tool_response": {
                            "tool_call_id": "call_b",
                            "is_error": True,
                        },
                    }
                )
            )
            await socket.send(
                json.dumps(
                    {
                        "type": "agent_response",
                        "agent_response_event": {
                            "agent_response": "private answer",
                            "response_id": "reply_a",
                        },
                    }
                )
            )
            await socket.send(
                json.dumps(
                    {
                        "type": "agent_response_correction",
                        "agent_response_correction_event": {
                            "event_id": 14,
                            "response_id": "reply_a",
                            "original_agent_response": "private answer",
                            "corrected_agent_response": "private…",
                        },
                    }
                )
            )
            await socket.wait_closed()
        finally:
            closed.set()

    stream = io.StringIO()
    async with asyncio.timeout(5), serve(peer, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        url = f"ws://127.0.0.1:{port}/convai?conversation_signature=private-signature"
        async with Conversation.open(
            url, _OPTIONS, trace=EventTrace(stream), allow_loopback=True
        ) as session:
            assert session.metadata == Metadata("conv_test", "pcm_16000", "pcm_16000")
            await session.send_text("private question")
            a, b, no_reply = [await session.receive() for _ in range(3)]
            assert isinstance(a, ClientToolCall) and a.tool_call_id == "call_a"
            assert isinstance(b, ClientToolCall) and b.tool_call_id == "call_b"
            assert (
                isinstance(no_reply, ClientToolCall) and not no_reply.expects_response
            )
            with pytest.raises(ValueError, match="No pending"):
                await session.send_tool_result(no_reply.tool_call_id, "never sent")
            await session.send_tool_result(
                b.tool_call_id, "private error", is_error=True
            )
            await session.send_tool_result(a.tool_call_id, "private result")
            with pytest.raises(ValueError, match="No pending"):
                await session.send_tool_result(a.tool_call_id, "duplicate")
            assert await session.receive() == AgentToolResponse("call_b", True)
            assert await session.receive() == AgentResponse(
                "private answer", None, "reply_a"
            )
            assert await session.receive() == AgentResponseCorrection(
                "private answer", "private…", 14, "reply_a"
            )
        await closed.wait()
    assert observed == [
        {
            "type": "conversation_initiation_client_data",
            "conversation_config_override": {
                "agent": {
                    "prompt": {
                        "prompt": "private prompt",
                        "tool_ids": ["tool_clock"],
                        "tool_ids_strict": True,
                    }
                },
                "conversation": {"text_only": True},
            },
        },
        {"type": "pong", "event_id": 12},
        {"type": "user_message", "text": "private question"},
        {
            "type": "client_tool_result",
            "tool_call_id": "call_b",
            "is_error": True,
            "result": "private error",
        },
        {
            "type": "client_tool_result",
            "tool_call_id": "call_a",
            "is_error": False,
            "result": "private result",
        },
        {"type": "pong", "event_id": 13},
    ]
    assert '"signed_url": "[redacted]"' in stream.getvalue()
    assert '"type": "ignored_event"' in stream.getvalue()
    assert "ignored_extension" not in stream.getvalue()
    assert "private" not in stream.getvalue()
    # The mock server is allowed to log its own wire; the client never logs it.
    assert not [
        row
        for row in caplog.records
        if row.name == "websockets.client"
        or row.name.endswith("voice_agent._socket.wire")
    ]


async def test_localhost_session_delivers_audio_transcript_and_interruption() -> None:
    pcm = b"\x10\xff\0\0"
    encoded = base64.b64encode(pcm).decode()
    observed: list[object] = []

    async def peer(socket: ServerConnection) -> None:
        await socket.recv()
        await socket.send(json.dumps(_METADATA))
        observed.append(json.loads(await socket.recv()))
        await socket.send(
            json.dumps(
                {
                    "type": "audio",
                    "audio_event": {"audio_base_64": encoded, "event_id": 7},
                }
            )
        )
        await socket.send('{"type":"interruption","interruption_event":{"event_id":7}}')
        await socket.send(
            json.dumps(
                {
                    "type": "user_transcript",
                    "user_transcription_event": {
                        "user_transcript": "hello",
                    },
                }
            )
        )

    async with asyncio.timeout(5), serve(peer, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        async with Conversation.open(
            f"ws://127.0.0.1:{port}", SessionOptions("", ()), allow_loopback=True
        ) as session:
            await session.send_audio(pcm)
            assert await session.receive() == Audio(pcm, 7)
            assert await session.receive() == Interruption(7)
            assert await session.receive() == UserTranscript("hello", None)
            with pytest.raises(EOFError, match="ended the conversation"):
                await session.receive()
            with pytest.raises(EOFError, match="ended the conversation"):
                await session.send_text("after close")
    assert observed == [{"user_audio_chunk": encoded}]


async def test_normal_close_before_metadata_is_a_setup_failure() -> None:
    async def peer(socket: ServerConnection) -> None:
        await socket.recv()
        await socket.close()

    async with asyncio.timeout(5), serve(peer, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        with pytest.raises(ConvAIError, match="before initialization"):
            async with Conversation.open(
                f"ws://127.0.0.1:{port}", _OPTIONS, allow_loopback=True
            ):
                pytest.fail("closed handshake returned a conversation")


async def test_cancelled_tool_result_closes_the_conversation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()
    closed = asyncio.Event()

    async def peer(socket: ServerConnection) -> None:
        await socket.recv()
        await socket.send(json.dumps(_METADATA))
        await socket.send(_tool("call_a"))
        await socket.wait_closed()
        closed.set()

    async def stall_send(_socket: ClientConnection, _payload: str) -> None:
        started.set()
        await asyncio.Future[None]()

    async with asyncio.timeout(5), serve(peer, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        async with Conversation.open(
            f"ws://127.0.0.1:{port}", _OPTIONS, allow_loopback=True
        ) as session:
            assert isinstance(await session.receive(), ClientToolCall)
            with monkeypatch.context() as patch:
                patch.setattr(ClientConnection, "send", stall_send)
                send = asyncio.create_task(session.send_tool_result("call_a", "utc"))
                await started.wait()
                send.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await send
            await closed.wait()
            with pytest.raises(EOFError, match="ended the conversation"):
                await session.send_text("cannot reuse this conversation")


async def test_missing_metadata_times_out_and_closes_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("punt_vox.voxd.voice_agent.session._METADATA_TIMEOUT_S", 0.05)
    closed = asyncio.Event()

    async def peer(socket: ServerConnection) -> None:
        await socket.recv()
        await socket.wait_closed()
        closed.set()

    async with asyncio.timeout(5), serve(peer, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        with pytest.raises(ConvAIError, match="Timed out waiting for ElevenLabs"):
            async with Conversation.open(
                f"ws://127.0.0.1:{port}", _OPTIONS, allow_loopback=True
            ):
                pytest.fail("uninitialized handshake returned a conversation")
        await closed.wait()


@pytest.mark.parametrize(
    ("wire", "diagnostic"),
    [
        ("a" * (MAX_MESSAGE_BYTES + 1), "WebSocket receive failed"),
        (b"bytes", "unexpected binary"),
        ("invalid-json with a private token", "invalid event JSON"),
        ('{"type":"client_error","message":"private token"}', "client_error"),
        ('{"type":"ping","ping_event":{"event_id":true}}', "integer event_id"),
        (
            '{"type":"interruption","interruption_event":{"event_id":1}}',
            "before initialization",
        ),
    ],
    ids=["oversize", "binary", "json", "vendor-error", "boolean-id", "pre-init-event"],
)
async def test_localhost_bad_handshake_closes_socket(
    wire: str | bytes, diagnostic: str
) -> None:
    closed = asyncio.Event()

    async def peer(socket: ServerConnection) -> None:
        await socket.recv()
        await socket.send(wire)
        await socket.wait_closed()
        closed.set()

    async with asyncio.timeout(5), serve(peer, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        with pytest.raises(ConvAIError, match=diagnostic) as exc:
            async with Conversation.open(
                f"ws://127.0.0.1:{port}", _OPTIONS, allow_loopback=True
            ):
                pytest.fail("failed handshake returned a conversation")
        assert "private" not in str(exc.value)
        await closed.wait()
