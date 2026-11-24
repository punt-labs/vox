"""The daemon log of one voice call: its start, going live, each tool, its end."""

from __future__ import annotations

import json
import logging
import time
from contextlib import contextmanager
from typing import TYPE_CHECKING, Self, final

from punt_vox.voxd.voice_call.log_text import LogText

if TYPE_CHECKING:
    from collections.abc import Callable, Generator
    from pathlib import Path

    from punt_vox.voxd.voice_call.call_outcome import CallOutcome

__all__ = ["CallLog"]

logger = logging.getLogger(__name__)


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
        outcome, elapsed = self._outcome(), time.monotonic() - self._started
        note = LogText.reason(outcome.error_note)
        ending = f"{outcome}, duration {elapsed:.1f} s{note}, trace {self._trace}"
        logger.info("Call ended: %s", ending)

    @staticmethod
    @contextmanager
    def starting(msg: dict[str, object]) -> Generator[None]:
        """Log the request's input mode and echo declaration, then its setup.

        A setup that fails before the call's task exists logs one end line
        naming the exception type only: a setup failure's message may carry
        a trace path or provider detail. The exception propagates.
        """
        logger.info(
            "Call starting: input %s, echo control %s",
            "script" if "script" in msg else "mic",
            LogText.echo_control(msg.get("echo_control", "none")),
        )
        try:
            yield
        except Exception as exc:
            logger.info("Call not started: %s", type(exc).__name__)
            raise

    @staticmethod
    def live(conversation_id: str) -> None:
        """Log the conversation the agent answered on."""
        logger.info("Call live: conversation %s", conversation_id)

    @staticmethod
    def tool(name: str, result: str, *, is_error: bool) -> None:
        """Log one answered client tool call; an error names its redacted text."""
        if is_error:
            error: str = json.loads(result)["error"]
            verdict = f"error {LogText.reason(error)}"
        else:
            verdict = "ok"
        logger.info("Call tool %s: %s", LogText.tool_name(name), verdict)
