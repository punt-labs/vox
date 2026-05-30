"""Typed payloads for Claude Code event-specific hook stdin data."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import cast

from punt_vox.hook_envelope import HookEnvelope


@dataclass(frozen=True, slots=True)
class StopPayload:
    """Payload for stop hook events."""

    stop_hook_active: bool
    cwd: Path | None = None  # PY-TS-14: absent for pre-field / malformed payloads

    @classmethod
    def parse(cls, data: dict[str, object]) -> StopPayload:
        """Return a StopPayload extracted from raw hook data."""
        raw = data.get("stop_hook_active", False)
        active = raw is True or raw == 1
        return cls(stop_hook_active=active, cwd=HookEnvelope.cwd_of(data))


@dataclass(frozen=True, slots=True)
class BashPayload:
    """Payload for post-bash hook events."""

    exit_code: int | None  # PY-TS-14: absent when tool_response omits it
    stdout: str
    cwd: Path | None = None  # PY-TS-14: absent for pre-field / malformed payloads

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
        return cls(exit_code=exit_code, stdout=stdout, cwd=HookEnvelope.cwd_of(data))


@dataclass(frozen=True, slots=True)
class NotificationPayload:
    """Payload for notification hook events."""

    notification_type: str
    message: str
    cwd: Path | None = None  # PY-TS-14: absent for pre-field / malformed payloads

    @classmethod
    def parse(cls, data: dict[str, object]) -> NotificationPayload:
        """Return a NotificationPayload extracted from raw hook data."""
        notification_type = data.get("notification_type", "unknown")
        if not isinstance(notification_type, str):
            notification_type = "unknown"
        message = data.get("message", "Needs your attention")
        if not isinstance(message, str):
            message = "Needs your attention"
        cwd = HookEnvelope.cwd_of(data)
        return cls(notification_type=notification_type, message=message, cwd=cwd)


HookPayload = StopPayload | BashPayload | NotificationPayload

__all__ = [
    "BashPayload",
    "HookPayload",
    "NotificationPayload",
    "StopPayload",
]
