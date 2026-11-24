"""Scripted caller audio for a voice call: recorded turns paced in real time."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final, Self, cast, final

from punt_vox.voxd.conversation_mode.audio_chunk import SAMPLE_RATE_HZ

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

__all__ = ["ScriptInput", "ScriptTurn"]

_FRAME_S: Final = 0.02
_FRAME_BYTES: Final = round(SAMPLE_RATE_HZ * _FRAME_S) * 2  # S16LE mono
_SILENCE: Final = bytes(_FRAME_BYTES)
_MAX_TURN_BYTES: Final = 60 * SAMPLE_RATE_HZ * 2  # one minute of speech
_MAX_PAUSE_S: Final = 120.0
_MAX_TURNS: Final = 20


@dataclass(frozen=True, slots=True)
class ScriptTurn:
    """One injected utterance and the silence sent after it."""

    pcm: bytes
    pause_s: float


@final
class ScriptInput:
    """Speak recorded PCM in real time, as a person would, then hold silence.

    Silence is streamed between and after the turns because the agent's turn
    detection needs a continuous stream; a gap in the audio is not a pause.
    """

    __slots__ = ("_turns",)
    _turns: tuple[ScriptTurn, ...]

    def __new__(cls, turns: tuple[ScriptTurn, ...]) -> Self:
        """Bind the scripted turns, in order."""
        self = super().__new__(cls)
        self._turns = turns
        return self

    @classmethod
    def from_wire(cls, raw: object) -> Self:
        """Read ``[{"pcm_path": abs path, "pause_s": seconds}, ...]`` files."""
        turns = cast("list[object]", raw) if isinstance(raw, list) else []
        if not 0 < len(turns) <= _MAX_TURNS:
            raise ValueError(f"script must be a list of 1 to {_MAX_TURNS} turns")
        return cls(tuple(cls._turn(item) for item in turns))

    @staticmethod
    def _turn(item: object) -> ScriptTurn:
        if not isinstance(item, dict):
            raise ValueError("each script turn must be an object")
        turn = cast("dict[str, object]", item)
        path, pause = turn.get("pcm_path"), turn.get("pause_s", 0.0)
        if not isinstance(path, str) or not (file := Path(path)).is_absolute():
            raise ValueError("pcm_path must be an absolute path")
        if not isinstance(pause, int | float) or not 0 <= pause <= _MAX_PAUSE_S:
            raise ValueError(f"pause_s must be between 0 and {_MAX_PAUSE_S:.0f}")
        if not file.is_file():
            raise ValueError("pcm_path must name a regular file")
        if file.stat().st_size > _MAX_TURN_BYTES:
            raise ValueError("a script turn holds at most one minute of PCM")
        return ScriptTurn(file.read_bytes(), float(pause))

    @staticmethod
    def open() -> None:
        """A script needs no device: the turns were read at construction."""

    @staticmethod
    def close() -> None:
        """A script holds nothing to release."""

    async def chunks(self) -> AsyncIterator[bytes]:
        """Yield 20 ms frames on a real-time schedule that does not drift."""
        loop = asyncio.get_running_loop()
        due = loop.time()
        for frame in self._frames(self._turns):
            yield frame
            due += _FRAME_S
            await asyncio.sleep(max(0.0, due - loop.time()))

    @staticmethod
    def _frames(turns: tuple[ScriptTurn, ...]) -> Iterator[bytes]:
        for turn in turns:
            for start in range(0, len(turn.pcm), _FRAME_BYTES):
                yield turn.pcm[start : start + _FRAME_BYTES].ljust(_FRAME_BYTES, b"\0")
            yield from (_SILENCE for _ in range(round(turn.pause_s / _FRAME_S)))
        while True:
            yield _SILENCE
