"""Typed server events delivered to the consumer of a Conv AI session."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "AgentResponse",
    "AgentResponseCorrection",
    "AgentToolResponse",
    "Audio",
    "ClientToolCall",
    "Event",
    "Interruption",
    "Metadata",
    "UserTranscript",
]


@dataclass(frozen=True, slots=True)
class Metadata:
    """Identify the initialized conversation and its audio formats."""

    conversation_id: str
    input_audio_format: str
    output_audio_format: str


@dataclass(frozen=True, slots=True)
class ClientToolCall:
    """Request local execution of one named client tool."""

    tool_call_id: str
    tool_name: str
    parameters: dict[str, object]
    expects_response: bool


@dataclass(frozen=True, slots=True)
class AgentToolResponse:
    """Acknowledge a tool result on the ElevenLabs side."""

    tool_call_id: str
    is_error: bool


@dataclass(frozen=True, slots=True)
class AgentResponse:
    """Deliver a completed agent text response."""

    text: str
    event_id: int | None
    response_id: str | None = None


@dataclass(frozen=True, slots=True)
class AgentResponseCorrection:
    """Replace the text of an interrupted response with what was spoken."""

    original_text: str
    corrected_text: str
    event_id: int
    response_id: str


@dataclass(frozen=True, slots=True)
class UserTranscript:
    """Deliver a final transcription of one user utterance."""

    text: str
    event_id: int | None


@dataclass(frozen=True, slots=True)
class Audio:
    """Deliver an audio chunk in the session's negotiated output format."""

    pcm: bytes
    event_id: int


@dataclass(frozen=True, slots=True)
class Interruption:
    """Signal that the consumer should discard queued agent audio."""

    event_id: int


type Event = (
    AgentResponse
    | AgentResponseCorrection
    | AgentToolResponse
    | Audio
    | ClientToolCall
    | Interruption
    | UserTranscript
)
