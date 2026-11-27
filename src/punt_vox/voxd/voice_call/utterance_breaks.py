"""Tell which chunk of agent audio begins a new utterance."""

from __future__ import annotations

from typing import Self, final

__all__ = ["UtteranceBreaks"]


@final
class UtteranceBreaks:
    """Decide where the agent's utterances begin in its stream of audio.

    ElevenLabs gives one ``event_id`` to a whole agent turn, and a turn can
    hold several utterances and tool calls, so the id alone misses the pauses
    inside it. The events do not: each ``agent_response`` arrives before the
    audio of its utterance, and a tool call is a pause for the agent to wait
    on. A chunk begins an utterance when such an event arrived since the last
    chunk, or when its turn differs from the last chunk's.
    """

    __slots__ = ("_event_arrived", "_turn")

    _event_arrived: bool
    _turn: int  # The turn the last chunk belonged to; -1 before any.

    def __new__(cls) -> Self:
        """Create a tracker that has seen no audio."""
        self = super().__new__(cls)
        self._event_arrived = False
        self._turn = -1
        return self

    def note_event(self) -> None:
        """Record an ``agent_response`` or a tool call: the next chunk is new."""
        self._event_arrived = True

    def begins(self, turn: int) -> bool:
        """Return whether a chunk of *turn* begins an utterance, and consume it."""
        starts = self._event_arrived or turn != self._turn
        self._event_arrived, self._turn = False, turn
        return starts

    def reset(self) -> None:
        """Forget the stream: the next sink starts a fresh one."""
        self._event_arrived, self._turn = False, -1
