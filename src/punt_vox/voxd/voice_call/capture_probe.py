"""Watch the first seconds of microphone audio for a dead capture."""

from __future__ import annotations

import asyncio
import logging
from array import array
from typing import TYPE_CHECKING, Final, Self, final

from punt_vox.voxd.voice_call.capture_level import CaptureLevel

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from punt_vox.voxd.conversation_mode.audio_chunk import AudioChunk

__all__ = ["CaptureProbe"]

logger = logging.getLogger(__name__)

_SILENCE_LIMIT_S: Final = 3.0  # All-zero samples this long means no capture.
_REMEDIES: Final = (
    "On macOS, allow the microphone for the daemon's process under System "
    "Settings > Privacy & Security > Microphone, or run voxd from a terminal "
    "that already holds the microphone grant"
)
_NO_SIGNAL: Final = (
    "no microphone signal: the microphone delivered only silence for "
    f"{_SILENCE_LIMIT_S:.0f} seconds. {_REMEDIES}"
)
_NO_AUDIO: Final = (
    "no microphone signal: the microphone delivered no audio for "
    f"{_SILENCE_LIMIT_S:.0f} seconds. {_REMEDIES}"
)


@final
class CaptureProbe:
    """Fail a microphone capture that delivers only zeros, and report its level.

    macOS returns all-zero samples, and no error, when the microphone is denied
    to the capturing process; PortAudio cannot tell. The probe logs the level
    of the first second and raises once the stream has been all zeros for
    :data:`_SILENCE_LIMIT_S`. It is inert once that limit has passed.
    """

    __slots__ = ("_heard", "_level", "_seen_s")
    _heard: bool
    _seen_s: float
    _level: CaptureLevel

    def __new__(cls) -> Self:
        """Start a probe that has heard nothing yet."""
        self = super().__new__(cls)
        self._heard, self._seen_s, self._level = False, 0.0, CaptureLevel()
        return self

    async def watch(self, chunks: AsyncIterator[AudioChunk]) -> AsyncIterator[bytes]:
        """Forward each captured block's PCM, failing a capture that stays silent.

        A stream that opens but never calls back is as dead as one that
        delivers zeros, so every wait for a block is bounded by the same limit.

        Raises:
            ValueError: When the first seconds hold only zero samples, or a
                block does not arrive within the limit.
        """
        while (chunk := await self._next(chunks)) is not None:
            self.observe(chunk.pcm, chunk.duration_s)
            yield chunk.pcm

    @staticmethod
    async def _next(chunks: AsyncIterator[AudioChunk]) -> AudioChunk | None:
        """Return the next block within the limit; None once the stream ends."""
        try:
            return await asyncio.wait_for(anext(chunks), _SILENCE_LIMIT_S)
        except StopAsyncIteration:
            return None
        except TimeoutError:
            logger.warning(_NO_AUDIO)
            raise ValueError(_NO_AUDIO) from None

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
        self._level.measure(samples, self._seen_s - duration_s, self._seen_s)
        if not self._heard and self._seen_s >= _SILENCE_LIMIT_S:
            logger.warning(_NO_SIGNAL)
            raise ValueError(_NO_SIGNAL)
