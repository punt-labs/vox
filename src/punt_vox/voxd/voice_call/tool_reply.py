"""One answered client tool call: the wire text for the agent, the error for the log."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Self

__all__ = ["ToolReply"]


@dataclass(frozen=True, slots=True)
class ToolReply:
    """What a tool call produced, structured once where the outcome is known.

    ``result`` is the JSON text sent back to the agent. ``error`` is the
    failure message when the tool failed, so no reader has to parse
    ``result`` to find it again.
    """

    result: str
    error: str | None  # None when the tool succeeded.

    @classmethod
    def ok(cls, result: str) -> Self:
        """Wrap a successful result's wire text."""
        return cls(result, None)

    @classmethod
    def failed(cls, error: str) -> Self:
        """Build the agent's ``{"error": ...}`` reply and keep the message."""
        return cls(json.dumps({"error": error}, ensure_ascii=False), error)

    @property
    def is_error(self) -> bool:
        """Return whether the tool failed; the agent is told so with the result."""
        return self.error is not None
