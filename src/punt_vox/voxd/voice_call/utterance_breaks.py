"""Tell which chunk of agent audio begins a new utterance."""

from __future__ import annotations

from typing import Self, final

__all__ = ["UtteranceBreaks"]


@final
class UtteranceBreaks:
    """Decide where the agent's utterances begin in its stream of audio.

    ElevenLabs gives one ``event_id`` to a whole agent turn, and a turn can
    pause for a tool call and go on, so the id alone misses that pause. Over
    51 real call traces (6240 audio chunks), 102 of 153 resumptions after a
    ``client_tool_call`` kept the turn's id. A chunk therefore begins an
    utterance when a tool call arrived since the last chunk, or when its turn
    differs from the last chunk's.

    ``agent_response`` is not a boundary. Of the 482 in those traces, 90 carry
    a new turn id (the id already marks them), 55 follow a tool call before
    its audio resumes (the tool call already marks them), and 337 arrive after
    audio of the same turn: 49 of those fall between two chunks of the audio
    that resumes after a tool pause, where the buffer is shallow, and marking
    one would turn a real stall into a pause. It arrives before its own
    audio in some turns and after it in others, so its position says nothing.
    """

    __slots__ = ("_tool_called", "_turn")

    _tool_called: bool
    _turn: int  # The turn the last chunk belonged to; -1 before any.

    def __new__(cls) -> Self:
        """Create a tracker that has seen no audio."""
        self = super().__new__(cls)
        self._tool_called = False
        self._turn = -1
        return self

    def note_tool_call(self) -> None:
        """Record a tool call: the agent pauses, and its next chunk is new."""
        self._tool_called = True

    def begins(self, turn: int) -> bool:
        """Return whether a chunk of *turn* begins an utterance, and consume it."""
        starts = self._tool_called or turn != self._turn
        self._tool_called, self._turn = False, turn
        return starts

    def reset(self) -> None:
        """Forget the stream: the next sink starts a fresh one."""
        self._tool_called, self._turn = False, -1
