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

import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Self

if TYPE_CHECKING:
    import logging

__all__ = ["LOG_DATE_FORMAT", "LOG_FORMAT", "LOG_MESSAGE_TYPE", "LogRecordWire"]

LOG_MESSAGE_TYPE = "log"

# The one line format both the daemon file handler and the client fallback use, so
# a shipped line and a fallback line grep identically.
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_STRING_FIELDS = ("role", "name", "level", "message")


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
        for field in _STRING_FIELDS:
            value = raw.get(field)
            if not isinstance(value, str):
                got = type(value).__name__
                msg = f"log frame field {field!r} must be a string, got {got}"
                raise ValueError(msg)
            values[field] = value
        created = raw.get("created")
        if isinstance(created, bool) or not isinstance(created, int | float):
            got = type(created).__name__
            msg = f"log frame field 'created' must be a number, got {got}"
            raise ValueError(msg)
        return cls(
            role=values["role"],
            name=values["name"],
            level=values["level"],
            created=float(created),
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
