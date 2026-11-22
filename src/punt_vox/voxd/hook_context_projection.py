"""Bounded fields retained from redacted Claude Code hook payloads."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from string import hexdigits
from typing import TypedDict, cast

HOOK_TOOL_EVENTS = frozenset({"PreToolUse", "PostToolUse", "PostToolUseFailure"})


class HookToolResult(TypedDict):
    """Curated outcome of one hook tool event."""

    relay_seq: int
    tool: str
    result: str


class HookToolSubject:
    """Match retries without exposing redacted input values."""

    @staticmethod
    def fingerprint(
        payload: dict[str, object],
        *,
        key: bytes | None = None,  # None for unkeyed hashes of sanitized inputs.
    ) -> str:
        """Hash canonical tool input, keying the hash if it contains secrets."""
        raw = payload.get("tool_input")
        canonical = json.dumps(
            raw if isinstance(raw, dict) else {}, ensure_ascii=True, sort_keys=True
        ).encode("ascii")
        if key is not None:
            return hmac.digest(key, canonical, "sha256").hex()
        return hashlib.sha256(canonical).hexdigest()

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> str:
        """Prefer a valid sender fingerprint for clipped or redacted inputs."""
        subject = payload.get("relay_tool_subject")
        if (
            isinstance(subject, str)
            and len(subject) == 64
            and all(char in hexdigits for char in subject)
        ):
            return subject.lower()
        return cls.fingerprint(payload)


class HookText:
    """Project bounded, UTF-8-safe text from sanitized JSON."""

    LIMIT = 1000

    @staticmethod
    def clip(value: object, *, tail: bool = False) -> str:
        """Keep at most the useful head or tail of a text field."""
        if not isinstance(value, str):
            return ""
        if len(value) > HookText.LIMIT:
            value = (
                "..." + value[-HookText.LIMIT :]
                if tail
                else value[: HookText.LIMIT] + "..."
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
        subject = HookToolSubject.from_payload(payload)
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

    def as_result(self, sequence: int) -> HookToolResult:
        """Expose only curated tool details in the snapshot."""
        return {"relay_seq": sequence, "tool": self.tool, "result": self.result}
