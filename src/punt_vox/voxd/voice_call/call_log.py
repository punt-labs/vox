"""The daemon log of one voice call: its start, going live, each tool, its end."""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import TYPE_CHECKING, Final, Self, final

from punt_vox.voxd.log_text import LogText
from punt_vox.voxd.voice_call.boss_tools import BossTools
from punt_vox.voxd.voice_call.mic_input import MicInput

if TYPE_CHECKING:
    from collections.abc import Callable, Generator
    from pathlib import Path

    from punt_vox.voxd.voice_call.call_outcome import CallOutcome
    from punt_vox.voxd.voice_call.tool_reply import ToolReply

__all__ = ["CallLog"]

logger = logging.getLogger(__name__)

_TOOLS: Final = frozenset(tool.name for tool in BossTools.CATALOG)


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
        self._trace, self._outcome, self._started = trace, outcome, time.monotonic()
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
        mode = ("mic", "script")["script" in msg]
        echo = MicInput.echo_label(msg.get("echo_control", "none"))
        logger.info("Call starting: input %s, echo control %s", mode, echo)
        try:
            yield
        except Exception as exc:
            logger.info("Call not started: %s", type(exc).__name__)
            raise

    @staticmethod
    def live(conversation_id: str) -> None:
        """Log the conversation the agent answered on; the peer named it."""
        logger.info("Call live: conversation %s", LogText.reason(conversation_id))

    @classmethod
    def tool(cls, name: str, answer: ToolReply) -> None:
        """Log one answered client tool call; an error names its redacted text."""
        tool = cls._tool_name(name)
        if answer.error is None:
            logger.info("Call tool %s: ok", tool)
        else:
            logger.info("Call tool %s: error %s", tool, LogText.reason(answer.error))

    @classmethod
    def unreadable(cls, name: str, exc: ValueError) -> None:
        """Log a tool error reply the wire could not parse, by exception type only.

        The exception's message may quote the wire text, so it stays out.
        """
        logger.info(
            "Call tool %s: unreadable error reply (%s)",
            cls._tool_name(name),
            type(exc).__name__,
        )

    @staticmethod
    def _tool_name(name: str) -> str:
        return name if name in _TOOLS else "unknown"  # An agent-chosen name.
