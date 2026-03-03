#!/usr/bin/env python3
"""Generate per-signal chime MP3 assets.

Each chime is a short (0.5-1.2s) synthesized tone designed to be
instantly distinguishable by ear:

- tests_pass:      bright ascending two-note (C5→G5)
- tests_fail:      low descending two-note (G3→C3)
- lint_pass:       crisp single high ping (E6)
- lint_fail:       dull low thud (A2)
- git_push_ok:     triumphant three-note arpeggio (C5→E5→G5)
- merge_conflict:  dissonant two-tone alert (C4+C#4)

Requires: pydub (already a project dependency).
Output:   assets/chime_<signal>.mp3
"""

from __future__ import annotations

import math
import struct
from pathlib import Path

from pydub import AudioSegment  # type: ignore[import-untyped]

ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
SAMPLE_RATE = 44100
CHANNELS = 1
SAMPLE_WIDTH = 2  # 16-bit


def _sine_wave(freq_hz: float, duration_ms: int, volume_db: float = -10.0) -> AudioSegment:  # type: ignore[return]
    """Generate a pure sine wave as an AudioSegment."""
    n_samples = int(SAMPLE_RATE * duration_ms / 1000)
    max_val = 2 ** (SAMPLE_WIDTH * 8 - 1) - 1

    # Apply volume (dBFS)
    amplitude = max_val * (10 ** (volume_db / 20))

    raw = b"".join(
        struct.pack("<h", int(amplitude * math.sin(2 * math.pi * freq_hz * i / SAMPLE_RATE)))
        for i in range(n_samples)
    )

    seg: AudioSegment = AudioSegment(  # pyright: ignore[reportUnknownMemberType]
        data=raw,
        sample_width=SAMPLE_WIDTH,
        frame_rate=SAMPLE_RATE,
        channels=CHANNELS,
    )
    return seg


def _fade(seg: AudioSegment, fade_in_ms: int = 15, fade_out_ms: int = 50) -> AudioSegment:  # type: ignore[return]
    """Apply fade in/out to avoid clicks."""
    result: AudioSegment = seg.fade_in(fade_in_ms).fade_out(fade_out_ms)  # pyright: ignore[reportUnknownMemberType]
    return result


# Note frequencies (Hz)
C3 = 130.81
G3 = 196.00
A2 = 110.00
C4 = 261.63
CS4 = 277.18  # C#4
C5 = 523.25
E5 = 659.25
G5 = 783.99
E6 = 1318.51


def chime_tests_pass() -> AudioSegment:  # type: ignore[return]
    """Bright ascending two-note: C5 → G5."""
    note1 = _fade(_sine_wave(C5, 200, volume_db=-12.0))
    note2 = _fade(_sine_wave(G5, 300, volume_db=-10.0))
    silence = AudioSegment.silent(duration=50)  # pyright: ignore[reportUnknownMemberType]
    result: AudioSegment = note1 + silence + note2  # pyright: ignore[reportUnknownMemberType]
    return result


def chime_tests_fail() -> AudioSegment:  # type: ignore[return]
    """Low descending two-note: G3 → C3."""
    note1 = _fade(_sine_wave(G3, 250, volume_db=-10.0))
    note2 = _fade(_sine_wave(C3, 400, volume_db=-8.0))
    silence = AudioSegment.silent(duration=50)  # pyright: ignore[reportUnknownMemberType]
    result: AudioSegment = note1 + silence + note2  # pyright: ignore[reportUnknownMemberType]
    return result


def chime_lint_pass() -> AudioSegment:  # type: ignore[return]
    """Crisp single high ping: E6."""
    result: AudioSegment = _fade(_sine_wave(E6, 150, volume_db=-14.0), fade_in_ms=5, fade_out_ms=100)
    return result


def chime_lint_fail() -> AudioSegment:  # type: ignore[return]
    """Dull low thud: A2."""
    result: AudioSegment = _fade(_sine_wave(A2, 350, volume_db=-6.0), fade_in_ms=10, fade_out_ms=200)
    return result


def chime_git_push_ok() -> AudioSegment:  # type: ignore[return]
    """Triumphant three-note arpeggio: C5 → E5 → G5."""
    note1 = _fade(_sine_wave(C5, 150, volume_db=-12.0))
    note2 = _fade(_sine_wave(E5, 150, volume_db=-11.0))
    note3 = _fade(_sine_wave(G5, 350, volume_db=-10.0))
    gap = AudioSegment.silent(duration=30)  # pyright: ignore[reportUnknownMemberType]
    result: AudioSegment = note1 + gap + note2 + gap + note3  # pyright: ignore[reportUnknownMemberType]
    return result


def chime_merge_conflict() -> AudioSegment:  # type: ignore[return]
    """Dissonant two-tone alert: C4 + C#4 overlaid."""
    tone1 = _sine_wave(C4, 500, volume_db=-10.0)
    tone2 = _sine_wave(CS4, 500, volume_db=-10.0)
    chord: AudioSegment = tone1.overlay(tone2)  # pyright: ignore[reportUnknownMemberType]
    result: AudioSegment = _fade(chord, fade_in_ms=10, fade_out_ms=150)
    return result


CHIMES = {
    "chime_tests_pass": chime_tests_pass,
    "chime_tests_fail": chime_tests_fail,
    "chime_lint_pass": chime_lint_pass,
    "chime_lint_fail": chime_lint_fail,
    "chime_git_push_ok": chime_git_push_ok,
    "chime_merge_conflict": chime_merge_conflict,
}


def main() -> None:
    ASSETS_DIR.mkdir(exist_ok=True)
    for name, generator in CHIMES.items():
        path = ASSETS_DIR / f"{name}.mp3"
        audio = generator()
        audio.export(path, format="mp3")  # pyright: ignore[reportUnknownMemberType]
        print(f"  {path.name} ({path.stat().st_size:,} bytes)")
    print(f"\nGenerated {len(CHIMES)} chimes in {ASSETS_DIR}")


if __name__ == "__main__":
    main()
