"""Render voxd's ``call_status`` for a person watching a terminal."""

from __future__ import annotations

from typing import Self, cast, final

__all__ = ["CallStatusView"]

_TEXT_WIDTH = 72


@final
class CallStatusView:
    """One ``call_status`` snapshot: the live line and the end-of-call report."""

    __slots__ = ("_status",)
    _status: dict[str, object]

    def __new__(cls, status: object) -> Self:
        """Wrap a ``call_status`` result; anything but a dict reads as idle."""
        self = super().__new__(cls)
        self._status = status if isinstance(status, dict) else {"state": "idle"}
        return self

    @property
    def state(self) -> str:
        """Return the call state: idle, connecting, live, ended or failed."""
        return str(self._status.get("state", "idle"))

    @property
    def running(self) -> bool:
        """Return whether the call has not ended yet."""
        return self.state in {"connecting", "live"}

    @property
    def failed(self) -> bool:
        """Return whether the call ended in a failure."""
        return self.state == "failed"

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
        if self._status.get("error"):
            lines.append(f"error: {self._status['error']}")
        if self._status.get("trace"):
            lines.append(f"trace: {self._status['trace']}")
        return lines

    @staticmethod
    def _tool_line(call: dict[str, object]) -> str:
        """Return one tool call as ``tool: name(args) -> ok|error``."""
        outcome = "error" if call.get("error") else "ok"
        name, arguments = call.get("tool", "?"), call.get("arguments", {})
        return f"tool: {name}({arguments}) -> {outcome}"

    def _entries(self, key: str) -> list[dict[str, object]]:
        """Return the dict entries of a list field, skipping anything else."""
        value = self._status.get(key)
        entries = cast("list[object]", value) if isinstance(value, list) else []
        return [
            cast("dict[str, object]", entry)
            for entry in entries
            if isinstance(entry, dict)
        ]
