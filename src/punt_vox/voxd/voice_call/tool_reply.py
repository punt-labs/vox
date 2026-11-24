"""One answered client tool call: the wire text for the agent, the error for the log."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Self, cast

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

    @classmethod
    def from_wire(cls, result: str, *, is_error: bool) -> Self:
        """Read the error once from the wire text the tool runner produced.

        The runner speaks ``{"error": ...}`` for a failure; this is the one
        place that parses it, so the log and the call never do.
        """
        if not is_error:
            return cls(result, None)
        decoded: object = json.loads(result)  # Wire boundary: any JSON value.
        # Any shape but {"error": <text>} is one ValueError, so the caller has
        # a single failure to contain; a KeyError or TypeError would escape it.
        if isinstance(decoded, dict):
            error = cast("dict[str, object]", decoded).get("error")
            if isinstance(error, str):
                return cls(result, error)
        raise ValueError("tool error reply is not an error object")

    @property
    def is_error(self) -> bool:
        """Return whether the tool failed; the agent is told so with the result."""
        return self.error is not None
