from __future__ import annotations

from punt_vox.voxd.voice_agent._decode import EventDecoder, Message
from punt_vox.voxd.voice_agent.events import AgentResponse, UserTranscript


def test_response_and_transcript_can_omit_legacy_ids() -> None:
    response = Message({"agent_response_event": {"agent_response": "hello"}})
    transcript = Message({"user_transcription_event": {"user_transcript": "hi"}})

    assert EventDecoder.decode("agent_response", response) == AgentResponse(
        "hello", None
    )
    assert EventDecoder.decode("user_transcript", transcript) == UserTranscript(
        "hi", None
    )
