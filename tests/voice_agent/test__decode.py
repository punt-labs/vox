from __future__ import annotations

import pytest

from punt_vox.voxd.voice_agent import ConvAIError
from punt_vox.voxd.voice_agent._decode import EventDecoder, Message


def test_decode_rejects_invalid_pcm_base64() -> None:
    message = Message({"audio_event": {"audio_base_64": "!!!!", "event_id": 1}})
    with pytest.raises(ConvAIError, match="invalid base64 audio"):
        EventDecoder.decode("audio", message)


def test_decode_rejects_unpairable_tool_call() -> None:
    message = Message(
        {
            "client_tool_call": {
                "tool_name": "clock",
                "tool_call_id": "",
                "parameters": {},
            }
        }
    )
    with pytest.raises(ConvAIError, match="empty tool_call_id"):
        EventDecoder.decode("client_tool_call", message)
