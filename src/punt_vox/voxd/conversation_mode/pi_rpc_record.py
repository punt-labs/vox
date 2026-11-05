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
from typing import TYPE_CHECKING, Self, cast, final

if TYPE_CHECKING:
    from collections.abc import Iterator

    from punt_vox.voxd.conversation_mode.secret_path_policy import SecretPathPolicy

__all__ = ["MalformedRecordError", "PiRpcRecord"]

# The argument keys through which pi's read-only tools name a path or a glob.
_PATH_ARGS = ("path", "glob", "pattern")

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

        Two shapes fail: a ``response`` with ``success: false`` (the command
        was refused), and an assistant ``message_end`` whose ``stopReason``
        is ``error`` (the provider call failed after the prompt was taken).
        """
        if self.kind == "response" and self._fields.get("success") is False:
            error = self._fields.get("error")
            return error if isinstance(error, str) and error else "command refused"
        if self.kind == "message_end":
            message = self._mapping(self._fields.get("message"))
            if message.get("role") == "assistant" and message.get("stopReason") in {
                "error",
                "aborted",
            }:
                error = message.get("errorMessage")
                return error if isinstance(error, str) and error else "reply failed"
        return ""

    def touches(self, policy: SecretPathPolicy) -> bool:
        """Return whether this record shows the agent reaching a denied path."""
        return any(policy.denies(candidate) for candidate in self._path_candidates())

    def _path_candidates(self) -> Iterator[str]:
        """Yield every path or glob this record shows a tool being aimed at.

        Tool arguments are checked at ``toolcall_end`` and
        ``tool_execution_start``; a grep result is also checked line by line,
        because a broad search (no path at all) can still land a match inside
        a secret file, and its output names that file before the match text.
        """
        yield from self._tool_arguments()
        if self.kind == "tool_execution_end" and self._fields.get("toolName") == "grep":
            for line in self._result_text().splitlines():
                yield line.split(":", 1)[0]

    def _tool_arguments(self) -> Iterator[str]:
        if self.kind == "tool_execution_start":
            args = self._mapping(self._fields.get("args"))
        elif self._assistant_event_kind() == "toolcall_end":
            call = self._mapping(self._assistant_event().get("toolCall"))
            args = self._mapping(call.get("arguments"))
        else:
            return
        for key in _PATH_ARGS:
            value = args.get(key)
            if isinstance(value, str):
                yield value

    def _result_text(self) -> str:
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
