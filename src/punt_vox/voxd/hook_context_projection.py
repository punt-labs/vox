"""Bounded fields retained from redacted Claude Code hook payloads."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import cast

_TEXT_LIMIT = 1000


class HookText:
    """Project bounded, UTF-8-safe text from sanitized JSON."""

    @staticmethod
    def clip(value: object, *, tail: bool = False) -> str:
        """Keep at most the useful head or tail of a text field."""
        if not isinstance(value, str):
            return ""
        if len(value) > _TEXT_LIMIT:
            value = (
                "..." + value[-_TEXT_LIMIT:] if tail else value[:_TEXT_LIMIT] + "..."
            )
        return value.encode("utf-8", errors="replace").decode("utf-8")

    @classmethod
    def result(cls, value: object) -> str:
        """Prefer shell output text and compact other tool results."""
        if isinstance(value, str):
            return cls.clip(value, tail=True)
        if isinstance(value, dict):
            values = cast("dict[str, object]", value)
            if "stdout" in values or "stderr" in values:
                return cls.clip(
                    "\n".join(
                        part
                        for field in ("stdout", "stderr")
                        if (part := cls.clip(values.get(field), tail=True))
                    ),
                    tail=True,
                )
        return (
            cls.clip(json.dumps(value, ensure_ascii=True), tail=True)
            if value is not None
            else ""
        )


@dataclass(frozen=True, slots=True)
class ToolUpdate:
    """The small useful portion of a tool event, not the raw input/output."""

    tool: str
    subject: str
    file: str
    result: str
    failed: bool
    completed: bool

    @classmethod
    def from_payload(cls, payload: dict[str, object], event: str) -> ToolUpdate:
        """Project Claude's explicit success/failure and file-path fields."""
        raw = payload.get("tool_input")
        tool_input = cast("dict[str, object]", raw) if isinstance(raw, dict) else {}
        file = HookText.clip(tool_input.get("file_path"))
        subject = (
            file
            or HookText.clip(tool_input.get("command"))
            or HookText.clip(tool_input.get("path"))
        )
        failed = event == "PostToolUseFailure"
        if failed:
            result = HookText.clip(payload.get("error"), tail=True) or "tool failed"
        else:
            result = HookText.result(payload.get("tool_response"))
        return cls(
            HookText.clip(payload.get("tool_name")),
            subject,
            file,
            result,
            failed,
            event != "PreToolUse",
        )

    def as_result(self, sequence: int) -> dict[str, object]:
        """Expose only curated tool details in the snapshot."""
        return {"relay_seq": sequence, "tool": self.tool, "result": self.result}
