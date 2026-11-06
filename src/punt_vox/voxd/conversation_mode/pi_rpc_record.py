"""One JSONL record read from ``pi --mode rpc``'s stdout, and what it means to a call.

The RPC stream carries far more than a call needs -- system and user message
echoes, usage blocks, thinking deltas, queue updates. :class:`PiRpcRecord`
wraps one parsed record and answers only the questions the call agent host
asks of it: is this reply text, a tool event, the end of the run, a failure?
Unknown record types answer "none of the above", never an error -- pi adds
event types over time, and the host must keep reading through them.

Shapes follow pi's own reference (``docs/json.md``, ``docs/rpc.md``) and were
confirmed against a live ``pi --mode rpc`` run: a tool call surfaces first as
``message_update``/``toolcall_end`` (carrying ``toolCall.name`` and
``toolCall.arguments``), then ``tool_execution_start`` (``toolName``,
``args``) in the same instant, then ``tool_execution_end`` (``result``).
"""

from __future__ import annotations

import json
from typing import Self, cast, final

__all__ = ["MalformedRecordError", "PiRpcRecord"]

_TOOL_EVENT_TYPES = frozenset(
    {"tool_execution_start", "tool_execution_update", "tool_execution_end"}
)


class MalformedRecordError(ValueError):
    """A stdout line was not one JSON object -- the wire contract has drifted."""


@final
class PiRpcRecord:
    """One parsed RPC record, interpreted for the call agent host."""

    __slots__ = ("_fields",)
    # A wire boundary: a decoded JSON object, narrowed field by field below.
    _fields: dict[str, object]

    def __new__(cls, fields: dict[str, object]) -> Self:
        self = super().__new__(cls)
        self._fields = fields
        return self

    @classmethod
    def parse(cls, line: bytes) -> Self:
        """Parse one stdout line, raising :class:`MalformedRecordError` if it is not."""
        try:
            decoded: object = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            msg = f"malformed pi rpc line: {line[:200]!r}"
            raise MalformedRecordError(msg) from exc
        if not isinstance(decoded, dict):
            msg = f"pi rpc line is not a JSON object: {line[:200]!r}"
            raise MalformedRecordError(msg)
        return cls(cast("dict[str, object]", decoded))

    @property
    def kind(self) -> str:
        kind = self._fields.get("type")
        return kind if isinstance(kind, str) else ""

    @property
    def is_settled(self) -> bool:
        """Whether pi will do no more automatic work for this prompt."""
        return self.kind == "agent_settled"

    @property
    def is_aborted(self) -> bool:
        """Whether this ``agent_settled`` reports the run ended by being aborted."""
        return self.is_settled and self._fields.get("aborted") is True

    @property
    def is_retry_start(self) -> bool:
        """Whether pi is about to retry an attempt the provider failed."""
        return self.kind == "auto_retry_start"

    @property
    def is_tool_event(self) -> bool:
        """Whether this record reports a tool starting, progressing, or ending."""
        return self.kind in _TOOL_EVENT_TYPES or self._assistant_event_kind() in {
            "toolcall_start",
            "toolcall_end",
        }

    def text_delta(self) -> str:
        """Return the assistant reply text this record appends, or ``""``."""
        event = self._assistant_event()
        if event.get("type") != "text_delta":
            return ""
        delta = event.get("delta")
        return delta if isinstance(delta, str) else ""

    def failure(self) -> str:
        """Return why this record reports a failed command or reply, or ``""``.

        Three shapes fail: a ``response`` with ``success: false`` (the command
        was refused), an assistant ``message_end`` whose ``stopReason`` is
        ``error`` or ``aborted`` (the provider call failed after the prompt
        was taken), and an ``auto_retry_end`` with ``success: false`` (pi
        retried that failure and every attempt failed). An errored
        ``message_end`` that pi goes on to retry is superseded by the retry;
        deciding that is the caller's job (:attr:`is_retry_start`).
        """
        if self.kind in {"response", "auto_retry_end"}:
            return self._unsuccessful()
        if self.kind == "message_end":
            message = self._mapping(self._fields.get("message"))
            if message.get("role") == "assistant" and message.get("stopReason") in {
                "error",
                "aborted",
            }:
                error = message.get("errorMessage")
                return error if isinstance(error, str) and error else "reply failed"
        return ""

    def extension_error(self) -> str:
        """Return an ``extension_error`` record's extension, event, and error."""
        if self.kind != "extension_error":
            return ""
        path = self._text(self._fields.get("extensionPath"))
        event = self._text(self._fields.get("event"))
        return f"{path} ({event}): {self._text(self._fields.get('error'))}"

    @property
    def tool_call_id(self) -> str:
        """The id correlating a tool call's start and end records, or ``""``."""
        return self._text(self._fields.get("toolCallId"))

    @property
    def tool_name(self) -> str:
        return self._text(self._fields.get("toolName"))

    @property
    def is_error(self) -> bool:
        """Whether a ``tool_execution_end`` reports the call failed or was blocked."""
        return self._fields.get("isError") is True

    def tool_args(self) -> dict[str, object]:
        """Return a ``tool_execution_start`` record's arguments, or ``{}``."""
        return self._mapping(self._fields.get("args"))

    def result_text(self) -> str:
        """Return a ``tool_execution_end`` record's text result, or ``""``."""
        result = self._mapping(self._fields.get("result"))
        content = result.get("content")
        if not isinstance(content, list):
            return ""
        blocks = cast("list[object]", content)
        return "\n".join(
            text
            for block in blocks
            if isinstance(text := self._mapping(block).get("text"), str)
        )

    def _unsuccessful(self) -> str:
        """Return why a ``success: false`` record failed, or ``""`` if it succeeded."""
        if self._fields.get("success") is not False:
            return ""
        error = self._fields.get("error", self._fields.get("finalError"))
        return error if isinstance(error, str) and error else f"{self.kind} failed"

    def error_text(self) -> str:
        """Return a failed or blocked tool call's error text, or ``""``."""
        failed = self.kind == "tool_execution_end" and self.is_error
        return self.result_text() if failed else ""

    @staticmethod
    def _text(value: object) -> str:
        return value if isinstance(value, str) else ""

    def _assistant_event(self) -> dict[str, object]:
        if self.kind != "message_update":
            return {}
        return self._mapping(self._fields.get("assistantMessageEvent"))

    def _assistant_event_kind(self) -> str:
        kind = self._assistant_event().get("type")
        return kind if isinstance(kind, str) else ""

    @staticmethod
    def _mapping(value: object) -> dict[str, object]:
        """Return *value* as a JSON object, or empty when it is any other shape."""
        return cast("dict[str, object]", value) if isinstance(value, dict) else {}
