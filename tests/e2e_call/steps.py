"""Script steps the fake ElevenLabs Conversational AI agent plays in order."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

__all__ = [
    "ExpectAudioChunks",
    "ExpectPong",
    "HangUp",
    "SendAgentResponse",
    "SendAudio",
    "SendInterruption",
    "SendPing",
    "SendToolCall",
    "SendToolResponse",
    "SendUserTranscript",
    "Step",
    "WaitForLogLine",
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
class SendToolResponse:
    """Acknowledge a tool the agent ran, as the server does after each tool.

    A ``system`` tool named ``end_call`` is the agent hanging up: the real
    server sends this acknowledgement and then closes the socket.
    """

    name: str
    tool_call_id: str
    tool_type: str = "client"
    is_error: bool = False
    is_called: bool = True  # False acknowledges a tool the agent did not run.


@dataclass(frozen=True, slots=True)
class SendInterruption:
    """Tell the client to discard queued agent audio."""

    event_id: int


@dataclass(frozen=True, slots=True)
class SendPing:
    """Send the server's keepalive ping the client must answer with a pong."""

    event_id: int


@dataclass(frozen=True, slots=True)
class ExpectPong:
    """Block until the client has answered the ping with this event id."""

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
class WaitForLogLine:
    """Block until the daemon's log file contains this text.

    What the socket cannot show, the daemon's log can: that it opened a
    device and played audio. Waiting longer than ``timeout_s`` is a fault.
    """

    path: Path
    text: str
    timeout_s: float = 10.0


@dataclass(frozen=True, slots=True)
class HangUp:
    """Close the socket from the agent side."""


type Step = (
    SendAudio
    | SendUserTranscript
    | SendAgentResponse
    | SendToolCall
    | SendToolResponse
    | SendInterruption
    | SendPing
    | ExpectPong
    | WaitForToolResult
    | ExpectAudioChunks
    | WaitForLogLine
    | HangUp
)
