"""How one voice call ended, summarized the way its log line reads."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

__all__ = ["CallEnd", "CallOutcome", "CallState"]

type CallState = Literal["connecting", "live", "ended", "failed"]


class CallEnd(StrEnum):
    """Who ended a call that ended without failing."""

    AGENT = "agent"  # The agent ran its built-in end_call tool.
    PEER = "peer"  # ElevenLabs closed the conversation.
    CALLER = "caller"  # call_stop, or the daemon cancelling the call.


@dataclass(frozen=True, slots=True)
class CallOutcome:
    """How a call ended."""

    state: CallState
    error: str | None  # None unless the call failed.
    tool_calls: int
    ended_by: CallEnd | None = None  # None until the call ended, and for a failure.

    def __str__(self) -> str:
        """Read as ``<state>[ by <who>], <n> tool call(s)``."""
        plural = "" if self.tool_calls == 1 else "s"
        ending = (
            self.state if self.ended_by is None else f"{self.state} by {self.ended_by}"
        )
        return f"{ending}, {self.tool_calls} tool call{plural}"

    @property
    def error_note(self) -> str:
        """Return ``, error <error>`` for a failed call, else nothing."""
        return "" if self.error is None else f", error {self.error}"
