"""Socket delivery precedes sanitized tracing, including failure paths."""

from __future__ import annotations

import io
import json
from unittest.mock import AsyncMock

import pytest
from websockets.asyncio.client import ClientConnection

from punt_vox.voxd.voice_agent.trace import EventTrace
from punt_vox.voxd.voice_agent.writer import ConversationWriter


async def test_wire_payload_is_not_copied_into_default_trace() -> None:
    socket = AsyncMock(spec=ClientConnection)
    stream = io.StringIO()
    await ConversationWriter(socket, EventTrace(stream)).send(
        {"type": "user_message", "text": "private words"}, "user_message"
    )
    assert json.loads(socket.send.call_args.args[0])["text"] == "private words"
    assert json.loads(stream.getvalue())["detail"] == {}
    assert "private words" not in stream.getvalue()


async def test_selected_trace_fields_and_optional_trace() -> None:
    socket = AsyncMock(spec=ClientConnection)
    stream = io.StringIO()
    await ConversationWriter(socket, EventTrace(stream)).send(
        {"type": "user_message", "text": ""}, "user_message", {"length": 0}
    )
    assert json.loads(stream.getvalue())["detail"] == {"length": 0}
    await ConversationWriter(socket, None).send(
        {"type": "contextual_update", "text": ""}, "contextual_update"
    )
    assert socket.send.await_count == 2


async def test_socket_failure_propagates_without_recording_a_success() -> None:
    socket = AsyncMock(spec=ClientConnection)
    socket.send.side_effect = OSError("closed")
    stream = io.StringIO()
    with pytest.raises(OSError, match="closed"):
        await ConversationWriter(socket, EventTrace(stream)).send(
            {"type": "user_message", "text": "hello"}, "user_message"
        )
    assert stream.getvalue() == ""
