"""The recent exchange of one voice call, as its status reply shows it."""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING, Final, Self, final

from punt_vox.voxd.voice_agent.events import (
    AgentResponse,
    AgentResponseCorrection,
    UserTranscript,
)

if TYPE_CHECKING:
    type Turn = dict[str, str]  # {"role": "user" | "agent", "text": ...}

__all__ = ["CallTranscript"]

_HISTORY: Final = 20


@final
class CallTranscript:
    """Keep the last turns, rewriting an interrupted agent reply in place."""

    __slots__ = ("_turns",)
    _turns: deque[tuple[str | None, Turn]]  # (response_id, turn); users have none.

    def __new__(cls) -> Self:
        """Start an empty exchange capped at the last turns a status shows."""
        self = super().__new__(cls)
        self._turns = deque(maxlen=_HISTORY)
        return self

    def record(
        self, event: UserTranscript | AgentResponse | AgentResponseCorrection
    ) -> None:
        """Append a user or agent turn, or correct an agent turn already kept."""
        match event:
            case UserTranscript(text=text):
                self._turns.append((None, {"role": "user", "text": text}))
            case AgentResponse(text=text, response_id=response_id):
                self._turns.append((response_id, {"role": "agent", "text": text}))
            case _:  # What remains of the union is a correction.
                self._correct(event)

    def turns(self) -> list[Turn]:
        """Return the kept turns, oldest first."""
        return [turn for _, turn in self._turns]

    def _correct(self, correction: AgentResponseCorrection) -> None:
        """Replace interrupted agent text with what the person actually heard."""
        for response_id, turn in reversed(self._turns):
            if response_id == correction.response_id:
                turn["text"] = correction.corrected_text
                return
