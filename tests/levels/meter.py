"""Measure loudness, peaks, and silence with ffmpeg; render playback with mpv.

ffmpeg's ``ebur128`` filter (ITU-R BS.1770 / EBU R128) and ``silencedetect``
are the oracle, so the checks do not reuse vox's own pydub pipeline.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

SILENCE_DB = -50.0
"""Level below which audio counts as silence (dBFS)."""

_SUMMARY = re.compile(r"Summary:(?P<body>.*)", re.DOTALL)
_INTEGRATED = re.compile(r"I:\s+(?P<value>-?[\d.]+|-inf) LUFS")
_TRUE_PEAK = re.compile(r"Peak:\s+(?P<value>-?[\d.]+|-inf) dBFS")
_SILENCE_START = re.compile(r"silence_start: (?P<value>-?[\d.]+)")
_SILENCE_END = re.compile(r"silence_end: (?P<value>-?[\d.]+)")


@dataclass(frozen=True, slots=True)
class Loudness:
    """Integrated loudness (LUFS) and true peak (dBTP) of one span."""

    integrated_lufs: float
    true_peak_dbtp: float


@dataclass(frozen=True, slots=True)
class Silence:
    """One span below ``SILENCE_DB``, in seconds from the start."""

    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


def require(binary: str) -> str:
    path = shutil.which(binary)
    if path is None:
        # Opt-in tier: a missing binary is a broken run, not a pass.
        pytest.fail(f"signal check prerequisite missing: {binary}")
    return path


def _ffmpeg(path: Path, audio_filter: str, span: tuple[float, float] | None) -> str:
    trim = [] if span is None else ["-ss", f"{span[0]:.3f}", "-t", f"{span[1]:.3f}"]
    result = subprocess.run(
        [
            require("ffmpeg"),
            "-hide_banner",
            "-nostats",
            *trim,
            "-i",
            str(path),
            "-af",
            audio_filter,
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stderr


def _number(pattern: re.Pattern[str], text: str, label: str) -> float:
    match = pattern.search(text)
    assert match is not None, f"ffmpeg printed no {label}"
    return float(match["value"])


def duration(path: Path) -> float:
    result = subprocess.run(
        [
            require("ffprobe"),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(result.stdout)


def loudness(path: Path, span: tuple[float, float] | None = None) -> Loudness:
    """Measure ``path``, or the ``(start, length)`` span of it, in seconds."""
    summary = _SUMMARY.search(_ffmpeg(path, "ebur128=peak=true", span))
    assert summary is not None, "ffmpeg printed no ebur128 summary"
    body = summary["body"]
    return Loudness(
        integrated_lufs=_number(_INTEGRATED, body, "integrated loudness"),
        true_peak_dbtp=_number(_TRUE_PEAK, body, "true peak"),
    )


def silences(path: Path, min_seconds: float) -> list[Silence]:
    """Return every span below ``SILENCE_DB`` lasting at least ``min_seconds``."""
    log = _ffmpeg(path, f"silencedetect=n={SILENCE_DB}dB:d={min_seconds}", None)
    starts = [float(m["value"]) for m in _SILENCE_START.finditer(log)]
    ends = [float(m["value"]) for m in _SILENCE_END.finditer(log)]
    # A file that ends in silence prints a start with no matching end.
    ends += [duration(path)] * (len(starts) - len(ends))
    return [Silence(start, end) for start, end in zip(starts, ends, strict=True)]


def leading_silence(path: Path) -> float:
    """Seconds before the first sound, using a 1 ms detection floor."""
    spans = silences(path, 0.001)
    first = spans[0] if spans else None
    return first.end if first is not None and first.start <= 0.0 else 0.0


def trailing_silence(path: Path) -> float:
    """Seconds after the last sound, using a 1 ms detection floor."""
    spans = silences(path, 0.001)
    last = spans[-1] if spans else None
    end = duration(path)
    return end - last.start if last is not None and last.end >= end - 0.001 else 0.0


def render(sources: list[Path], output: Path, volume: int) -> Path:
    """Play ``sources`` back to back through mpv at ``volume`` into a WAV file.

    Uses the gapless setting and volume voxd's music player starts mpv with,
    so the file holds what the listener hears, track change included.
    """
    subprocess.run(
        [
            require("mpv"),
            "--no-config",
            "--no-video",
            "--terminal=no",
            "--gapless-audio=yes",
            f"--volume={volume}",
            "--ao=pcm",
            f"--ao-pcm-file={output}",
            *(str(source) for source in sources),
        ],
        check=True,
        timeout=600,
    )
    return output


def transform(source: Path, output: Path, audio_filter: str) -> Path:
    """Write ``source`` through an ffmpeg filter, for fault-injection tests."""
    subprocess.run(
        [
            require("ffmpeg"),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-af",
            audio_filter,
            str(output),
        ],
        check=True,
    )
    return output


def tone(output: Path, seconds: float, gain_db: float, hertz: int = 440) -> Path:
    """Write a sine tone peaking at ``gain_db`` dBFS, a stand-in music track."""
    subprocess.run(
        [
            require("ffmpeg"),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"aevalsrc=sin({hertz}*2*PI*t):d={seconds}:s=44100",
            "-af",
            f"volume={gain_db}dB",
            str(output),
        ],
        check=True,
    )
    return output
