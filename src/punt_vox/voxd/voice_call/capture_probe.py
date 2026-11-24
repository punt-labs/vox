"""Watch the first seconds of microphone audio for a dead capture."""

from __future__ import annotations

import logging
import math
from array import array
from typing import TYPE_CHECKING, Final, Self, final

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from punt_vox.voxd.conversation_mode.audio_chunk import AudioChunk

__all__ = ["CaptureProbe"]

logger = logging.getLogger(__name__)

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
    """Fail a microphone capture that delivers only zeros, and report its level.

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

    async def watch(self, chunks: AsyncIterator[AudioChunk]) -> AsyncIterator[bytes]:
        """Forward each captured block's PCM, failing a capture that stays silent.

        Raises:
            ValueError: When the first seconds hold only zero samples.
        """
        async for chunk in chunks:
            self.observe(chunk.pcm, chunk.duration_s)
            yield chunk.pcm

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
