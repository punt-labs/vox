from __future__ import annotations

import io
import json
from typing import cast
from unittest.mock import AsyncMock

import pytest
from websockets.asyncio.client import ClientConnection

from punt_vox.voxd.voice_agent.events import Metadata
from punt_vox.voxd.voice_agent.session import Conversation
from punt_vox.voxd.voice_agent.trace import EventTrace


async def test_context_is_hidden_wire_event_with_private_trace() -> None:
    socket = AsyncMock(spec=ClientConnection)
    trace = io.StringIO()
    conv = Conversation(
        cast("ClientConnection", socket),
        Metadata("c", "pcm_16000", "pcm_16000"),
        EventTrace(trace),
    )
    await conv.send_context("private worker report")
    assert json.loads(socket.send.call_args.args[0]) == {
        "type": "contextual_update",
        "text": "private worker report",
    }
    assert json.loads(trace.getvalue())["type"] == "contextual_update"
    assert "private" not in trace.getvalue()
    socket.send.side_effect = OSError("closed")
    with pytest.raises(OSError, match="closed"):
        await conv.send_context("")
