"""Incremental PCM assembly for MP3 stitching."""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import Self, final

# pydub 0.25.1 uses unescaped parentheses in regex strings (utils.py lines
# 300, 301, 310, 314) which emit SyntaxWarning on Python 3.13 during bytecode
# compilation. This is an upstream bug; filter before importing pydub.
# Must match on message, not module — compile-time warnings ignore module filters.
warnings.filterwarnings(
    "ignore", category=SyntaxWarning, message=r"invalid escape sequence"
)
from pydub import AudioSegment as AudioSegment  # noqa: E402

_TRAILING_SILENCE_MS = 150
_TAIL_FADE_MS = 20


@final
class MP3Assembly:
    _head: AudioSegment
    _chunks: list[bytes]

    def __new__(cls, path: Path) -> Self:
        first = cls._read_segment(path)
        self = super().__new__(cls)
        self._head = first
        self._chunks = [first.raw_data]
        return self

    def append(self, segment: AudioSegment) -> None:
        head = self._head
        if (
            segment.channels > head.channels
            or segment.frame_rate > head.frame_rate
            or segment.sample_width > head.sample_width
        ):
            # Resample the prefix as one stream, just as pydub append does.
            # The format only grows, and MP3 has a fixed set of formats.
            self._head = self.build() + segment
            self._chunks = [self._head.raw_data]
        else:
            converted = (
                segment.set_channels(head.channels)
                .set_frame_rate(head.frame_rate)
                .set_sample_width(head.sample_width)
            )
            self._chunks.append(converted.raw_data)

    def append_file(self, path: Path, pause: AudioSegment) -> None:
        segment = self._read_segment(path)
        self.append(pause)
        self.append(segment)

    def build(self) -> AudioSegment:
        return AudioSegment(
            b"".join(self._chunks),
            sample_width=self._head.sample_width,
            frame_rate=self._head.frame_rate,
            channels=self._head.channels,
        )

    def export_padded(self, output_path: Path) -> None:
        """Fade the speech tail before appending the playback guard silence."""
        audio = self.build()
        fade_ms = min(len(audio), _TAIL_FADE_MS)
        faded = audio.fade_out(fade_ms) if fade_ms else audio
        tail = AudioSegment.silent(
            duration=_TRAILING_SILENCE_MS, frame_rate=self._head.frame_rate
        )
        (faded + tail).export(str(output_path), format="mp3")

    @staticmethod
    def _read_segment(path: Path) -> AudioSegment:
        if not path.exists():
            raise FileNotFoundError(f"Segment not found: {path}")
        return AudioSegment.from_mp3(str(path))
