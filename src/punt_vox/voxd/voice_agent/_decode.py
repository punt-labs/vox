"""Validate untrusted ElevenLabs frames before making typed events."""

from __future__ import annotations

import base64
import binascii
import json
from typing import TYPE_CHECKING, ClassVar, Self, cast, final

from punt_vox.voxd.voice_agent.events import (
    AgentResponse,
    AgentResponseCorrection,
    AgentToolResponse,
    Audio,
    ClientToolCall,
    Interruption,
    Metadata,
    Ping,
    UserTranscript,
)
from punt_vox.voxd.voice_agent.fault import ConvAIError

if TYPE_CHECKING:
    from collections.abc import Callable

    from punt_vox.voxd.voice_agent.events import Decoded


@final
class Message:
    """Read fields from a valid JSON object with checked value types."""

    __slots__ = ("_data",)
    _data: dict[str, object]  # Untrusted JSON; each event validates its own fields.

    def __new__(cls, data: object) -> Self:
        """Bind a JSON object for checked field access."""
        if not isinstance(data, dict):
            msg = "ElevenLabs event must be a JSON object"
            raise ConvAIError(msg)
        self = super().__new__(cls)
        self._data = cast("dict[str, object]", data)
        return self

    @classmethod
    def from_frame(cls, frame: bytes | str) -> Self:
        """Read a WebSocket JSON text frame."""
        if not isinstance(frame, str):
            msg = "ElevenLabs sent an unexpected binary frame"
            raise ConvAIError(msg)
        try:
            data: object = json.loads(frame)
        except (ValueError, RecursionError):
            msg = "ElevenLabs sent invalid event JSON"
            raise ConvAIError(msg) from None
        return cls(data)

    def text(self, name: str) -> str:
        """Return a string field or reject the malformed event."""
        value = self._data.get(name)
        if not isinstance(value, str):
            msg = f"ElevenLabs event requires string {name}"
            raise ConvAIError(msg)
        return value

    def number(self, name: str) -> int:
        """Return an integer field, not a boolean."""
        value = self._data.get(name)
        if not isinstance(value, int) or isinstance(value, bool):
            msg = f"ElevenLabs event requires integer {name}"
            raise ConvAIError(msg)
        return value

    def flag(
        self,
        name: str,
        *,
        default: bool | None = None,  # None makes the field required.
    ) -> bool:
        """Return a boolean field with an optional backwards-compatible default."""
        value = self._data.get(name, default)
        if not isinstance(value, bool):
            msg = f"ElevenLabs event requires boolean {name}"
            raise ConvAIError(msg)
        return value

    def optional_number(self, name: str) -> int | None:  # Wire field can be absent.
        """Allow older transcript and response events to omit their event ID."""
        return self.number(name) if name in self._data else None

    def optional_text(self, name: str) -> str | None:  # Wire field can be absent.
        """Return a string field when the event supplies it."""
        return self.text(name) if name in self._data else None

    def body(self, name: str) -> Self:
        """Return a nested event object."""
        value = self._data.get(name)
        if not isinstance(value, dict):
            msg = f"ElevenLabs event requires object {name}"
            raise ConvAIError(msg)
        return type(self)(cast("dict[str, object]", value))

    def fields(self) -> dict[str, object]:  # Client tools each define their own schema.
        """Copy this message's top-level fields."""
        return self._data.copy()


@final
class EventDecoder:
    """Convert known events and ignore extensions we do not consume."""

    @classmethod
    def subscriptions(cls) -> tuple[str, ...]:
        """Return the protocol events an ElevenLabs agent should send."""
        return (*cls._EVENTS, "client_error")

    @classmethod
    def decode(
        cls, kind: str, message: Message
    ) -> Decoded | None:  # None skips extensions.
        """Decode one event without letting remote error text reach logs."""
        if kind == "client_error":
            msg = "ElevenLabs sent a client_error event"
            raise ConvAIError(msg)
        if kind not in cls._EVENTS:
            return None
        key, decode = cls._EVENTS[kind]
        return decode(message.body(key))

    @staticmethod
    def _metadata(body: Message) -> Metadata:
        return Metadata(
            body.text("conversation_id"),
            body.text("user_input_audio_format"),
            body.text("agent_output_audio_format"),
        )

    @staticmethod
    def _audio(body: Message) -> Audio:
        try:
            pcm = base64.b64decode(body.text("audio_base_64"), validate=True)
        except (binascii.Error, ValueError):
            msg = "ElevenLabs sent invalid base64 audio"
            raise ConvAIError(msg) from None
        return Audio(pcm, body.number("event_id"))

    @staticmethod
    def _tool_call(body: Message) -> ClientToolCall:
        tool_call_id = body.text("tool_call_id")
        if not tool_call_id:
            msg = "ElevenLabs sent an empty tool_call_id"
            raise ConvAIError(msg)
        return ClientToolCall(
            tool_call_id,
            body.text("tool_name"),
            body.body("parameters").fields(),
            body.flag("expects_response", default=True),
        )

    _EVENTS: ClassVar[dict[str, tuple[str, Callable[[Message], Decoded]]]] = {
        "conversation_initiation_metadata": (
            "conversation_initiation_metadata_event",
            _metadata,
        ),
        "audio": ("audio_event", _audio),
        "client_tool_call": ("client_tool_call", _tool_call),
        "ping": ("ping_event", lambda body: Ping(body.number("event_id"))),
        "interruption": (
            "interruption_event",
            lambda body: Interruption(body.number("event_id")),
        ),
        "agent_response": (
            "agent_response_event",
            lambda body: AgentResponse(
                body.text("agent_response"),
                body.optional_number("event_id"),
                body.optional_text("response_id"),
            ),
        ),
        "agent_response_correction": (
            "agent_response_correction_event",
            lambda body: AgentResponseCorrection(
                body.text("original_agent_response"),
                body.text("corrected_agent_response"),
                body.number("event_id"),
                body.text("response_id"),
            ),
        ),
        "agent_tool_response": (
            "agent_tool_response",
            lambda body: AgentToolResponse(
                body.text("tool_call_id"), body.flag("is_error")
            ),
        ),
        "user_transcript": (
            "user_transcription_event",
            lambda body: UserTranscript(
                body.text("user_transcript"), body.optional_number("event_id")
            ),
        ),
    }
