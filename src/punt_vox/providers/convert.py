"""Shared conversion utilities for local TTS providers."""

from __future__ import annotations

import subprocess
from pathlib import Path

__all__ = ["ffmpeg_to_mp3", "rate_to_wpm"]

_DEFAULT_WPM = 175


def ffmpeg_to_mp3(input_path: Path, output_path: Path) -> None:
    """Convert an audio file to MP3 via ffmpeg."""
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(input_path),
            "-codec:a",
            "libmp3lame",
            "-qscale:a",
            "2",
            str(output_path),
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )


def rate_to_wpm(rate: int) -> int:
    """Convert percentage rate to words-per-minute.

    rate=100 -> 175 WPM (normal), rate=90 -> 157 WPM.
    """
    return max(1, int(_DEFAULT_WPM * rate / 100))
