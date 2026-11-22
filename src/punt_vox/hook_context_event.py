"""Validation and recursive redaction for live hook events."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

_CREDENTIAL_KEYS = (
    "token",
    "secret",
    "signed_url",
    "api_key",
    "apikey",
    "access_key",
    "authorization",
    "password",
    "private_key",
    "privatekey",
    "credential",
)
_LEDGER_FIELDS = frozenset({"recv_seq", "session_seq", "received_at", "received_ns"})
MAX_SEQUENCE = 2**63 - 1


class HookRedaction:
    """Replace credential-shaped values, including inside nested arrays."""

    @classmethod
    def clean(cls, payload: dict[str, object]) -> dict[str, object]:
        """Return a redacted copy."""
        return {
            key: "[redacted]" if cls._credential_key(key) else cls._value(value)
            for key, value in payload.items()
        }

    @staticmethod
    def _credential_key(key: str) -> bool:
        normalized = key.lower().replace("-", "_")
        return any(marker in normalized for marker in _CREDENTIAL_KEYS)

    @classmethod
    def _value(cls, value: object) -> object:
        if isinstance(value, dict):
            return cls.clean(cast("dict[str, object]", value))
        if isinstance(value, list):
            return [cls._value(item) for item in cast("list[object]", value)]
        return value


@dataclass(frozen=True, slots=True)
class HookIdentity:
    """Identity of a live hook payload, excluding its transport sequence."""

    session: str
    event: str

    @classmethod
    def parse(cls, payload: dict[str, object]) -> HookIdentity:
        """Reject ledger records and require the live hook envelope."""
        if _LEDGER_FIELDS.intersection(payload):
            msg = "refusing to ingest a hook ledger record"
            raise ValueError(msg)
        session = payload.get("session_id")
        event = payload.get("hook_event_name")
        if not isinstance(session, str) or not session.strip() or len(session) > 256:
            msg = "hook payload requires a valid session_id"
            raise ValueError(msg)
        if not isinstance(event, str) or not event.strip() or len(event) > 64:
            msg = "hook payload requires a valid hook_event_name"
            raise ValueError(msg)
        return cls(session, event)
