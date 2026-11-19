"""Distinguish client roles in shared diagnostic records."""

from __future__ import annotations

import copy
import logging
from typing import Literal, Self

Role = Literal["hook", "mcp", "cli", "worker"]


class ClientRoleFilter(logging.Filter):
    """Prefix a handler-local record without mutating other handlers' input."""

    _prefix: str

    def __new__(cls, name: str = "") -> Self:
        """Create a role filter."""
        self = super().__new__(cls)
        self._prefix = f"client.{name}."
        return self

    def filter(self, record: logging.LogRecord) -> logging.LogRecord:
        """Return an independent record with the client role in its name."""
        result = copy.copy(record)
        result.name = self._prefix + record.name
        return result
