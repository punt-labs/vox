"""Write sanitized, monotonic JSON Lines traces for Conv AI conversations."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from typing import TYPE_CHECKING, Self, cast, final

if TYPE_CHECKING:
    from typing import TextIO

__all__ = ["EventTrace"]

_CREDENTIAL_KEYS = ("token", "secret", "signedurl", "signature", "apikey")


@final
class EventTrace:
    """Write trace records without retaining the caller's credential fields."""

    __slots__ = ("_started", "_stream")
    _stream: TextIO
    _started: float

    def __new__(cls, stream: TextIO) -> Self:
        """Bind to a caller-owned text stream."""
        self = super().__new__(cls)
        self._stream = stream
        self._started = time.monotonic()
        return self

    def record(self, direction: str, event: str, detail: Mapping[str, object]) -> None:
        """Write one trace row with recursive, case-insensitive redaction."""
        row = {
            "ms": round((time.monotonic() - self._started) * 1000, 2),
            "dir": direction,
            "type": event,
            "detail": self._scrub(detail),
        }
        self._stream.write(json.dumps(row) + "\n")
        self._stream.flush()

    @classmethod
    def _scrub(cls, value: object) -> object:
        if isinstance(value, Mapping):
            return {
                key: cls._field(key, child)
                for key, child in cast("Mapping[str, object]", value).items()
            }
        if isinstance(value, list | tuple):
            return [
                cls._scrub(child)
                for child in cast("list[object] | tuple[object, ...]", value)
            ]
        return value

    @classmethod
    def _field(cls, key: str, value: object) -> object:
        if cls._is_credential(key):
            return "[redacted]"
        if key == "choice":
            return value if value in ("approve", "deny") else None
        if key == "prompt_seq":
            return (
                value
                if isinstance(value, int) and not isinstance(value, bool) and value > 0
                else None
            )
        return cls._scrub(value)

    @staticmethod
    def _is_credential(key: str) -> bool:
        normalized = key.lower().replace("-", "").replace("_", "")
        return any(part in normalized for part in _CREDENTIAL_KEYS)
