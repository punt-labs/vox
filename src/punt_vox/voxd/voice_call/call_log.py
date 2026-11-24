"""The daemon log of one voice call: its start, going live, each tool, its end."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Self, final

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

__all__ = ["CallLog", "CallOutcome", "CallState"]

logger = logging.getLogger(__name__)

type CallState = Literal["connecting", "live", "ended", "failed"]


@dataclass(frozen=True, slots=True)
class CallOutcome:
    """How a call ended."""

    state: CallState
    error: str | None  # None unless the call failed.
    tool_calls: int


@final
class CallLog:
    """Log a call's lifecycle at INFO; leaving the context logs the call's end."""

    __slots__ = ("_outcome", "_started", "_trace")
    _outcome: Callable[[], CallOutcome]
    _trace: Path
    _started: float

    def __new__(cls, trace: Path, outcome: Callable[[], CallOutcome]) -> Self:
        """Start the clock on a call that writes its trace at ``trace``."""
        self = super().__new__(cls)
        self._trace, self._outcome = trace, outcome
        self._started = time.monotonic()
        return self

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        """Log the end, however the call ended: hang-up, failure or cancel."""
        outcome = self._outcome()
        logger.info(
            "Call ended: %s, %d tool call%s, duration %.1f s%s, trace %s",
            outcome.state,
            outcome.tool_calls,
            "" if outcome.tool_calls == 1 else "s",
            time.monotonic() - self._started,
            f", error {outcome.error}" if outcome.error is not None else "",
            self._trace,
        )

    @staticmethod
    def starting(msg: dict[str, object]) -> None:
        """Log a call_start request's input mode and echo declaration."""
        logger.info(
            "Call starting: input %s, echo control %s",
            "script" if "script" in msg else "mic",
            msg.get("echo_control", "none"),
        )

    @staticmethod
    def live(conversation_id: str) -> None:
        """Log the conversation the agent answered on."""
        logger.info("Call live: conversation %s", conversation_id)

    @staticmethod
    def tool(name: str, result: str, *, is_error: bool) -> None:
        """Log one answered client tool call; an error names its text."""
        if is_error:
            error: str = json.loads(result)["error"]
            logger.info("Call tool %s: error %s", name, error)
        else:
            logger.info("Call tool %s: ok", name)
