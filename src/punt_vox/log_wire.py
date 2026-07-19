"""The ``{"type":"log",…}`` wire schema shared by the client shipper and daemon sink.

A client process ships each of its log records to ``voxd`` as a send-only frame on
the WebSocket it already opens for real work; ``voxd`` re-emits the record into
its single file handler. This module owns the frame's shape and the two ends'
(de)serialization so the schema lives in one place (PY-IC-9), importable without
the heavy client or daemon stack.

The ``message`` is the already-``%``-interpolated final string computed on the
client, carried with ``args`` dropped -- the daemon never re-interpolates, so an
untrusted value that reached a client log call cannot forge a second record. JSON
transport encodes any embedded control byte, so a raw newline in ``message``
cannot forge a second *frame*; each sink escapes on write so it cannot forge a
second *line*.
"""

from __future__ import annotations

import math
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Self, get_args

if TYPE_CHECKING:
    import logging

__all__ = [
    "LOG_DATE_FORMAT",
    "LOG_FORMAT",
    "LOG_MESSAGE_TYPE",
    "LogRecordWire",
    "Role",
]

LOG_MESSAGE_TYPE = "log"

# Which client process shipped a frame. Defined here (the wire schema) so both the
# sender's role stamp and the receiver's role validation read one source of truth.
Role = Literal["hook", "mcp", "cli", "playback"]

# The one line format both the daemon file handler and the client fallback use, so
# a shipped line and a fallback line grep identically.
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Per-field length caps bound the daemon's ingestion path: a token-bearing client
# (reachable via ``voxd --host``) must not be able to make the daemon decode,
# format, and synchronously write a multi-MB record on its event loop, starving
# synthesis and playback. Metadata fields stay short; only the message is roomy.
_MAX_META_CHARS = 256
_MAX_MESSAGE_CHARS = 8192
_STRING_FIELD_CAPS: tuple[tuple[str, int], ...] = (
    ("role", _MAX_META_CHARS),
    ("name", _MAX_META_CHARS),
    ("level", _MAX_META_CHARS),
    ("message", _MAX_MESSAGE_CHARS),
)
_VALID_ROLES: frozenset[str] = frozenset(get_args(Role))
# Bound ``created`` to a sane epoch window: an out-of-range or non-finite value
# makes the daemon's ``time.localtime`` raise, silently dropping the record.
_MIN_CREATED = 0.0
_MAX_CREATED = 4_102_444_800.0  # 2100-01-01 UTC


@dataclass(frozen=True, slots=True)
class LogRecordWire:
    """One shipped log record: which process, which module, level, time, text."""

    role: str
    name: str
    level: str
    created: float
    message: str

    @classmethod
    def from_record(cls, record: logging.LogRecord, *, role: str) -> Self:
        """Capture *record* as a wire frame stamped with the shipping *role*.

        ``getMessage`` performs the ``%`` interpolation now, on the client, so the
        frame carries the final text and the daemon writes it verbatim.
        """
        return cls(
            role=role,
            name=record.name,
            level=record.levelname,
            created=record.created,
            message=record.getMessage(),
        )

    @classmethod
    def from_wire(cls, raw: Mapping[str, object]) -> Self:
        """Parse a shipped frame, raising ``ValueError`` on any malformed field.

        A value-producing parser fulfils its type or fails loud (PY-EH-8): the
        daemon sink turns the raised error into a metadata-only WARNING rather
        than writing a half-formed record.
        """
        values: dict[str, str] = {}
        for field, cap in _STRING_FIELD_CAPS:
            value = raw.get(field)
            if not isinstance(value, str):
                got = type(value).__name__
                msg = f"log frame field {field!r} must be a string, got {got}"
                raise ValueError(msg)
            if len(value) > cap:
                # Length only, never the value -- an oversized field is an
                # attack, and echoing it would leak attacker content into vox.log.
                msg = f"log frame field {field!r} too long: {len(value)} > {cap} chars"
                raise ValueError(msg)
            values[field] = value
        if values["role"] not in _VALID_ROLES:
            # Report the length, never the raw role -- echoing an invalid,
            # attacker-controlled value would defeat the metadata-only rejection.
            role_len = len(values["role"])
            msg = f"log frame field 'role' is not a known role ({role_len} chars)"
            raise ValueError(msg)
        created = raw.get("created")
        if isinstance(created, bool) or not isinstance(created, int | float):
            got = type(created).__name__
            msg = f"log frame field 'created' must be a number, got {got}"
            raise ValueError(msg)
        created = float(created)
        if not math.isfinite(created) or not _MIN_CREATED <= created <= _MAX_CREATED:
            msg = f"log frame field 'created' out of range: {created!r}"
            raise ValueError(msg)
        return cls(
            role=values["role"],
            name=values["name"],
            level=values["level"],
            created=created,
            message=values["message"],
        )

    def to_wire(self) -> dict[str, object]:
        """Return the JSON-serializable ``log`` frame."""
        return {
            "type": LOG_MESSAGE_TYPE,
            "role": self.role,
            "name": self.name,
            "level": self.level,
            "created": self.created,
            "message": self.message,
        }

    @property
    def qualified_name(self) -> str:
        """Return the daemon logger name that tags the origin process and module."""
        return f"client.{self.role}.{self.name}"

    def format_line(self) -> str:
        """Return the one-line rendering for the local fallback file.

        Matches :data:`LOG_FORMAT` so a fallback line and a shipped ``vox.log``
        line read identically. ``message`` is raw here; the append sink escapes
        the whole line so a smuggled newline stays one physical line.
        """
        stamp = time.strftime(LOG_DATE_FORMAT, time.localtime(self.created))
        return f"{stamp} [{self.level}] {self.qualified_name}: {self.message}"
