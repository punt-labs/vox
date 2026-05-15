"""Typed payloads for Claude Code hook stdin data."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

__all__ = [
    "BashPayload",
    "HookPayload",
    "NotificationPayload",
    "StopPayload",
    "parse_hook_payload",
]


@dataclass(frozen=True, slots=True)
class StopPayload:
    """Payload for stop hook events."""

    stop_hook_active: bool

    @classmethod
    def parse(cls, data: dict[str, object]) -> StopPayload:
        """Return a StopPayload extracted from raw hook data."""
        raw = data.get("stop_hook_active", False)
        return cls(stop_hook_active=raw is True or raw == 1)


@dataclass(frozen=True, slots=True)
class BashPayload:
    """Payload for post-bash hook events."""

    exit_code: int | None
    stdout: str

    @classmethod
    def parse(cls, data: dict[str, object]) -> BashPayload:
        """Return a BashPayload extracted from raw hook data."""
        raw_response = data.get("tool_response", {})
        tool_response: dict[str, object] = (
            cast("dict[str, object]", raw_response)
            if isinstance(raw_response, dict)
            else {}
        )
        exit_code_raw = tool_response.get("exit_code")
        stdout = str(tool_response.get("stdout", ""))
        exit_code: int | None
        if isinstance(exit_code_raw, int):
            exit_code = exit_code_raw
        elif isinstance(exit_code_raw, str):
            try:
                exit_code = int(exit_code_raw)
            except ValueError:
                exit_code = None
        else:
            exit_code = None
        return cls(exit_code=exit_code, stdout=stdout)


@dataclass(frozen=True, slots=True)
class NotificationPayload:
    """Payload for notification hook events."""

    notification_type: str
    message: str

    @classmethod
    def parse(cls, data: dict[str, object]) -> NotificationPayload:
        """Return a NotificationPayload extracted from raw hook data."""
        notification_type = data.get("notification_type", "unknown")
        if not isinstance(notification_type, str):
            notification_type = "unknown"
        message = data.get("message", "Needs your attention")
        if not isinstance(message, str):
            message = "Needs your attention"
        return cls(notification_type=notification_type, message=message)


HookPayload = StopPayload | BashPayload | NotificationPayload


def parse_hook_payload(data: dict[str, object], kind: str) -> HookPayload:
    """Parse raw hook stdin dict into a typed payload."""
    if kind == "stop":
        return StopPayload.parse(data)
    if kind == "post_bash":
        return BashPayload.parse(data)
    if kind == "notification":
        return NotificationPayload.parse(data)
    msg = f"Unknown hook kind: {kind!r}"
    raise ValueError(msg)
