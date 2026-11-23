"""Render voxd's ``call_status`` for a person watching a terminal."""

from __future__ import annotations

from typing import Literal, Required, Self, TypedDict, cast, final

from punt_vox.client_errors import VoxdProtocolError

__all__ = ["CallStatusView"]

_TEXT_WIDTH = 72
_STATES = frozenset({"idle", "connecting", "live", "ended", "failed"})


class _CallStatus(TypedDict, total=False):
    """The fields of voxd's ``call_status`` result this view reads."""

    state: Required[str]
    call_id: str
    trace: str
    error: str | None  # None unless the call failed.
    transcript: list[object]
    tool_calls: list[object]


@final
class CallStatusView:
    """One ``call_status`` snapshot: the live line and the end-of-call report."""

    __slots__ = ("_status",)
    _status: _CallStatus

    def __new__(cls, status: object) -> Self:
        """Wrap a ``call_status`` result.

        Raises:
            VoxdProtocolError: If the state or a supplied call ID is invalid.
        """
        fields = cast("dict[str, object]", status) if isinstance(status, dict) else {}
        if fields.get("state") not in _STATES:
            raise VoxdProtocolError(f"Unexpected call status: {status!r}")
        call_id = fields.get("call_id")
        if "call_id" in fields and (
            not isinstance(call_id, str) or not call_id.strip()
        ):
            raise VoxdProtocolError("Invalid call ID in voxd status")
        self = super().__new__(cls)
        self._status = cast("_CallStatus", fields)
        return self

    @property
    def state(self) -> str:
        """Return the call state: idle, connecting, live, ended or failed."""
        return self._status["state"]

    @property
    def running(self) -> bool:
        """Return whether the call has not ended yet."""
        return self.state in {"connecting", "live"}

    @property
    def failed(self) -> bool:
        """Return whether the call ended in a failure."""
        return self.state == "failed"

    @property
    def call_id(self) -> str:
        """Return the call's identifier, or empty when not provided."""
        return self._status.get("call_id", "")

    @property
    def trace(self) -> str:
        """Return the call's trace file, which names one call; empty when idle."""
        return str(self._status.get("trace", ""))

    def line(self) -> str:
        """Return the live one-line summary: state, tool calls, last turn."""
        parts = [f"call {self.state}", f"{len(self._entries('tool_calls'))} tool calls"]
        transcript = self._entries("transcript")
        if transcript:
            last = transcript[-1]
            text = " ".join(str(last.get("text", "")).split())
            if len(text) > _TEXT_WIDTH:
                text = text[: _TEXT_WIDTH - 3] + "..."
            parts.append(f"{last.get('role', '?')}: {text}")
        return " | ".join(parts)

    def report(self) -> list[str]:
        """Return the end-of-call lines: transcript, tool calls, error, trace."""
        lines = [
            f"{turn.get('role', '?')}: {turn.get('text', '')}"
            for turn in self._entries("transcript")
        ]
        lines.extend(self._tool_line(call) for call in self._entries("tool_calls"))
        if error := self._status.get("error"):
            lines.append(f"error: {error}")
        if self.trace:
            lines.append(f"trace: {self.trace}")
        return lines

    @staticmethod
    def _tool_line(call: dict[str, object]) -> str:
        """Return one tool call as ``tool: name(args) -> ok|error``."""
        outcome = "error" if call.get("error") else "ok"
        name, arguments = call.get("tool", "?"), call.get("arguments", {})
        return f"tool: {name}({arguments}) -> {outcome}"

    def _entries(
        self, key: Literal["transcript", "tool_calls"]
    ) -> list[dict[str, object]]:
        """Return the dict entries of a list field, skipping anything else."""
        value: object = self._status.get(key)
        entries = cast("list[object]", value) if isinstance(value, list) else []
        return [
            cast("dict[str, object]", entry)
            for entry in entries
            if isinstance(entry, dict)
        ]
