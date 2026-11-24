"""How one voice call ended, summarized the way its log line reads."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

__all__ = ["CallOutcome", "CallState"]

type CallState = Literal["connecting", "live", "ended", "failed"]


@dataclass(frozen=True, slots=True)
class CallOutcome:
    """How a call ended."""

    state: CallState
    error: str | None  # None unless the call failed.
    tool_calls: int

    def __str__(self) -> str:
        """Read as ``<state>, <n> tool call(s)``."""
        plural = "" if self.tool_calls == 1 else "s"
        return f"{self.state}, {self.tool_calls} tool call{plural}"

    @property
    def error_note(self) -> str:
        """Return ``, error <error>`` for a failed call, else nothing."""
        return "" if self.error is None else f", error {self.error}"
