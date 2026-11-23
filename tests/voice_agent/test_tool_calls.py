from __future__ import annotations

import pytest

from punt_vox.voxd.voice_agent.events import ClientToolCall
from punt_vox.voxd.voice_agent.tool_calls import ToolCalls


def test_tool_calls_admit_once_and_claim_one_response() -> None:
    calls = ToolCalls()
    event = ClientToolCall("id", "tool", {}, expects_response=True)
    assert calls.admit(event)
    assert not calls.admit(event)
    calls.claim("id")
    assert not calls.admit(event)
    with pytest.raises(ValueError, match="No pending"):
        calls.claim("id")


def test_unknown_and_response_free_calls_cannot_be_claimed() -> None:
    calls = ToolCalls()
    with pytest.raises(ValueError, match="No pending"):
        calls.claim("missing")
    assert calls.admit(ClientToolCall("silent", "tool", {}, expects_response=False))
    with pytest.raises(ValueError, match="No pending"):
        calls.claim("silent")
