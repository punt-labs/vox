"""Cut the dead air ElevenLabs leaves at the ends of a generated music track.

Generated tracks can open on a couple of seconds of silence and close on a
fade that decays into several more. The playback loop plays each part with a
fresh ``loadfile``, so one track's tail and the next one's lead add up to
audible dead air at every track change. ``EdgeTrimmedMusic`` wraps the music
provider and removes both once, as each track is generated, keeping a few
milliseconds of fade so the cut does not click.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Final, Self, final

from pydub.exceptions import CouldntDecodeError
from pydub.silence import detect_leading_silence

from punt_vox._audio import AudioSegment

if TYPE_CHECKING:
    from pathlib import Path

    from punt_vox.types import MusicProvider

__all__ = ["EdgeTrim", "EdgeTrimmedMusic"]

# The -50 dBFS floor the nightly levels check uses for silence.
_SILENCE_DBFS: Final = -50.0
_CHUNK_MS: Final = 10
_FADE_MS: Final = 10
# Below this much dead air in total, re-encoding costs more than it saves.
_MIN_CUT_MS: Final = 50
# ElevenLabs delivers mp3_44100_128; re-encode at the same rate.
_BITRATE: Final = "128k"


@final
class EdgeTrim:
    """Trim leading and trailing silence from an MP3 track in place."""

    __slots__ = ("_silence_dbfs",)
    _silence_dbfs: float

    def __new__(cls, silence_dbfs: float = _SILENCE_DBFS) -> Self:
        """Create a trim that treats audio below ``silence_dbfs`` as dead air."""
        self = super().__new__(cls)
        self._silence_dbfs = silence_dbfs
        return self

    def apply(self, path: Path) -> None:
        """Rewrite ``path`` without its dead air; leave it alone if there is none.

        An all-silent track is left as is: there is no sound to keep. So is one
        that does not decode, for the playback loop to skip as a bad file.
        """
        try:
            audio = AudioSegment.from_mp3(str(path))
        except CouldntDecodeError:
            return
        lead = self._silence(audio)
        if lead >= len(audio):
            return
        tail = self._silence(audio.reverse())
        if lead + tail < _MIN_CUT_MS:
            return
        kept = audio[lead : len(audio) - tail]
        kept.fade_in(_FADE_MS).fade_out(_FADE_MS).export(
            str(path), format="mp3", bitrate=_BITRATE
        )

    def _silence(self, audio: AudioSegment) -> int:
        """Milliseconds of silence at the start of ``audio``."""
        return detect_leading_silence(
            audio, silence_threshold=self._silence_dbfs, chunk_size=_CHUNK_MS
        )


@final
class EdgeTrimmedMusic:
    """A ``MusicProvider`` whose tracks come back without dead air at either end."""

    __slots__ = ("_inner", "_trim")
    _inner: MusicProvider
    _trim: EdgeTrim

    def __new__(cls, inner: MusicProvider, trim: EdgeTrim) -> Self:
        """Wrap ``inner`` so each track it writes is trimmed by ``trim``."""
        self = super().__new__(cls)
        self._inner = inner
        self._trim = trim
        return self

    async def generate_track(
        self, prompt: str, duration_ms: int, output_path: Path
    ) -> Path:
        """Generate the track with the wrapped provider, then trim it in place."""
        path = await self._inner.generate_track(prompt, duration_ms, output_path)
        await asyncio.to_thread(self._trim.apply, path)
        return path
