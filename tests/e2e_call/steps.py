"""Script steps the fake ElevenLabs Conversational AI agent plays in order."""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "ExpectAudioChunks",
    "HangUp",
    "SendAgentResponse",
    "SendAudio",
    "SendInterruption",
    "SendToolCall",
    "SendUserTranscript",
    "Step",
    "WaitForToolResult",
]


@dataclass(frozen=True, slots=True)
class SendAudio:
    """Send one agent audio chunk of raw PCM bytes."""

    pcm: bytes
    event_id: int


@dataclass(frozen=True, slots=True)
class SendUserTranscript:
    """Send the final transcript of one user utterance."""

    text: str


@dataclass(frozen=True, slots=True)
class SendAgentResponse:
    """Send a completed agent text response."""

    text: str
    event_id: int


@dataclass(frozen=True, slots=True)
class SendToolCall:
    """Ask the client to run one named client tool."""

    name: str
    tool_call_id: str
    # The tool defines its own JSON schema; the wire carries plain JSON.
    parameters: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SendInterruption:
    """Tell the client to discard queued agent audio."""

    event_id: int


@dataclass(frozen=True, slots=True)
class WaitForToolResult:
    """Block until the client answers the named tool call."""

    tool_call_id: str


@dataclass(frozen=True, slots=True)
class ExpectAudioChunks:
    """Block until the client has sent this many user audio chunks in total."""

    count: int


@dataclass(frozen=True, slots=True)
class HangUp:
    """Close the socket from the agent side."""


type Step = (
    SendAudio
    | SendUserTranscript
    | SendAgentResponse
    | SendToolCall
    | SendInterruption
    | WaitForToolResult
    | ExpectAudioChunks
    | HangUp
)
