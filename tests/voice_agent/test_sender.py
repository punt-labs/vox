"""Outgoing conversation frames preserve content without tracing private payloads."""

from __future__ import annotations

import base64
import io
import json
from unittest.mock import AsyncMock

import pytest
from websockets.asyncio.client import ClientConnection

from punt_vox.voxd.voice_agent.sender import ConversationSender
from punt_vox.voxd.voice_agent.trace import EventTrace


async def test_text_context_and_error_result_framing() -> None:
    socket = AsyncMock(spec=ClientConnection)
    stream = io.StringIO()
    sender = ConversationSender(socket, EventTrace(stream))
    await sender.send_text("")
    await sender.send_context("private context")
    await sender.send_tool_result("call-1", "private result", is_error=True)
    frames = [json.loads(call.args[0]) for call in socket.send.call_args_list]
    assert frames == [
        {"type": "user_message", "text": ""},
        {"type": "contextual_update", "text": "private context"},
        {
            "type": "client_tool_result",
            "tool_call_id": "call-1",
            "result": "private result",
            "is_error": True,
        },
    ]
    assert "private" not in stream.getvalue()
    assert json.loads(stream.getvalue().splitlines()[-1])["detail"] == {
        "tool_call_id": "call-1",
        "is_error": True,
    }


@pytest.mark.parametrize("pcm", [b"", b"\x00\x01\xff\xfe"])
async def test_audio_is_base64_encoded(pcm: bytes) -> None:
    socket = AsyncMock(spec=ClientConnection)
    await ConversationSender(socket, None).send_audio(pcm)
    frame = json.loads(socket.send.call_args.args[0])
    assert base64.b64decode(frame["user_audio_chunk"]) == pcm


async def test_write_error_reaches_caller() -> None:
    socket = AsyncMock(spec=ClientConnection)
    socket.send.side_effect = OSError("closed")
    with pytest.raises(OSError, match="closed"):
        await ConversationSender(socket, None).send_text("hello")
