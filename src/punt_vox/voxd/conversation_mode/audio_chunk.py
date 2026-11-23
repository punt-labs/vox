"""One slice of captured microphone audio, with caller-supplied timing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, final

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

__all__ = ["SAMPLE_RATE_HZ", "AudioChunk"]

# The sample rate every producer and consumer of ``AudioChunk.pcm`` agrees on:
# real microphone capture (:mod:`.mic_audio_source`) requests this rate from
# the audio device, and the ElevenLabs STT provider
# (:mod:`punt_vox.providers.elevenlabs_stt`) declares it on the wire via
# ``file_format="pcm_s16le_16"`` (ElevenLabs' name for exactly this format).
# 16kHz mono is standard for speech recognition -- above it buys no
# transcription-accuracy benefit for voice, only bandwidth.
SAMPLE_RATE_HZ = 16_000


@final
@dataclass(frozen=True, slots=True)
class AudioChunk:
    """16-bit signed mono PCM plus how long it spans, as the caller measured it.

    The producer supplies ``duration_s`` with the samples.
    ``pcm`` is sampled at :data:`SAMPLE_RATE_HZ`.
    """

    pcm: bytes
    duration_s: float

    @classmethod
    async def as_async_iter(
        cls, chunks: Sequence[AudioChunk]
    ) -> AsyncIterator[AudioChunk]:
        """Return an async iterator over *chunks*."""
        for chunk in chunks:
            yield chunk
