"""Typed payloads for Claude Code event-specific hook stdin data."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

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


HookPayload = StopPayload | NotificationPayload

__all__ = [
    "HookPayload",
    "NotificationPayload",
    "StopPayload",
]
