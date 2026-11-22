from __future__ import annotations

import pytest

from punt_vox.voxd.voice_agent import ConvAIError
from punt_vox.voxd.voice_agent._decode import EventDecoder, Message


@pytest.mark.parametrize(
    ("body", "diagnostic"),
    [
        ([], "event must be a JSON object"),
        ({}, "requires object agent_tool_response"),
        ({"agent_tool_response": {"tool_call_id": 9}}, "requires string tool_call_id"),
        (
            {"agent_tool_response": {"tool_call_id": "c", "is_error": "false"}},
            "requires boolean is_error",
        ),
    ],
    ids=["not-an-object", "no-payload", "bad-call-id", "bad-error-flag"],
)
def test_tool_ack_rejects_invalid_shape(body: object, diagnostic: str) -> None:
    with pytest.raises(ConvAIError, match=diagnostic):
        EventDecoder.decode("agent_tool_response", Message(body))
