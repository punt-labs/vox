"""Caller audio for a voice call: the microphone, or a scripted injection."""

from __future__ import annotations

import asyncio
import logging
import math
from array import array
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final, Self, cast, final

from punt_vox.voxd.conversation_mode.audio_chunk import SAMPLE_RATE_HZ
from punt_vox.voxd.conversation_mode.mic_audio_source import MicAudioSource

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

__all__ = ["MicInput", "ScriptInput", "ScriptTurn"]

logger = logging.getLogger(__name__)

_FRAME_S: Final = 0.02
_FRAME_BYTES: Final = round(SAMPLE_RATE_HZ * _FRAME_S) * 2  # S16LE mono
_SILENCE: Final = bytes(_FRAME_BYTES)
_MAX_TURN_BYTES: Final = 60 * SAMPLE_RATE_HZ * 2  # one minute of speech
_MAX_PAUSE_S: Final = 120.0
_MAX_TURNS: Final = 20
_LEVEL_WINDOW_S: Final = 1.0  # Report the capture level once, over this much audio.
_SILENCE_LIMIT_S: Final = 3.0  # All-zero samples this long means no capture.
_NO_SIGNAL: Final = (
    "no microphone signal: the microphone delivered only silence for "
    f"{_SILENCE_LIMIT_S:.0f} seconds. On macOS, allow the microphone for the "
    "daemon's process under System Settings > Privacy & Security > Microphone, "
    "or run voxd from a terminal that already holds the microphone grant"
)


@final
class CaptureProbe:
    """Watch the first seconds of microphone audio for a dead capture.

    macOS returns all-zero samples, and no error, when the microphone is denied
    to the capturing process; PortAudio cannot tell. The probe logs the level
    of the first second and raises once the stream has been all zeros for
    :data:`_SILENCE_LIMIT_S`. It is inert once that limit has passed.
    """

    __slots__ = ("_heard", "_peak", "_samples", "_seen_s", "_squares")
    _heard: bool
    _seen_s: float
    _peak: int
    _squares: int
    _samples: int

    def __new__(cls) -> Self:
        """Start a probe that has heard nothing yet."""
        self = super().__new__(cls)
        self._heard, self._seen_s = False, 0.0
        self._peak, self._squares, self._samples = 0, 0, 0
        return self

    def observe(self, pcm: bytes, duration_s: float) -> None:
        """Account for one captured block.

        Raises:
            ValueError: When the capture has been silent for the whole limit.
        """
        if self._seen_s >= _SILENCE_LIMIT_S:
            return
        samples = array("h", pcm[: len(pcm) - len(pcm) % 2])
        self._seen_s += duration_s
        self._heard = self._heard or any(samples)
        self._measure(samples, self._seen_s - duration_s)
        if not self._heard and self._seen_s >= _SILENCE_LIMIT_S:
            logger.warning(_NO_SIGNAL)
            raise ValueError(_NO_SIGNAL)

    def _measure(self, samples: array[int], window_start_s: float) -> None:
        """Fold a block into the first-second level; log once the second is in."""
        if window_start_s >= _LEVEL_WINDOW_S:
            return
        self._samples += len(samples)
        self._peak = max(self._peak, max(map(abs, samples), default=0))
        self._squares += sum(sample * sample for sample in samples)
        if self._seen_s >= _LEVEL_WINDOW_S:
            rms = math.isqrt(self._squares // self._samples) if self._samples else 0
            logger.info(
                "Call capture level over the first second: peak %d, rms %d",
                self._peak,
                rms,
            )


@final
class MicInput:
    """Stream the system microphone for as long as the call consumes it."""

    __slots__ = ("_source",)
    _source: MicAudioSource

    def __new__(
        cls,
        source: MicAudioSource | None = None,  # None: the default device.
    ) -> Self:
        """Bind a microphone source; None selects the default input device."""
        self = super().__new__(cls)
        self._source = source if source is not None else MicAudioSource()
        return self

    @classmethod
    def with_echo_control(cls, declaration: object) -> Self:
        """Create live input only after the caller declares echo protection."""
        if declaration not in ("headset", "aec"):
            raise ValueError(
                "Live calls require echo protection: wear a headset and use "
                "'vox call --headset', or configure system echo cancellation "
                "and use 'vox call --aec'"
            )
        return cls()

    async def chunks(self) -> AsyncIterator[bytes]:
        """Yield each captured block's PCM, failing a capture that stays silent.

        Raises:
            ValueError: When the first seconds hold only zero samples.
        """
        probe = CaptureProbe()
        async for chunk in self._source.chunks():
            probe.observe(chunk.pcm, chunk.duration_s)
            yield chunk.pcm


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
        if not isinstance(path, str) or not Path(path).is_absolute():
            raise ValueError("pcm_path must be an absolute path")
        if not isinstance(pause, int | float) or not 0 <= pause <= _MAX_PAUSE_S:
            raise ValueError(f"pause_s must be between 0 and {_MAX_PAUSE_S:.0f}")
        file = Path(path)
        if not file.is_file():
            raise ValueError("pcm_path must name a regular file")
        if file.stat().st_size > _MAX_TURN_BYTES:
            raise ValueError("a script turn holds at most one minute of PCM")
        return ScriptTurn(file.read_bytes(), float(pause))

    async def chunks(self) -> AsyncIterator[bytes]:
        """Yield 20 ms frames on a real-time schedule that does not drift."""
        loop = asyncio.get_running_loop()
        due = loop.time()
        for frame in self._frames():
            yield frame
            due += _FRAME_S
            await asyncio.sleep(max(0.0, due - loop.time()))

    def _frames(self) -> Iterator[bytes]:
        for turn in self._turns:
            for start in range(0, len(turn.pcm), _FRAME_BYTES):
                yield turn.pcm[start : start + _FRAME_BYTES].ljust(_FRAME_BYTES, b"\0")
            yield from (_SILENCE for _ in range(round(turn.pause_s / _FRAME_S)))
        while True:
            yield _SILENCE
