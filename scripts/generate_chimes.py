#!/usr/bin/env python3
"""Generate the notification chime MP3 assets.

Each chime is a short (0.5-1.2s) synthesized tone designed to be
instantly distinguishable by ear:

- done:            warm resolution chord (C4→E4→G4)
- prompt:          gentle attention ping (A5→E5)

Requires: pydub (already a project dependency), ffmpeg with libmp3lame.
Output:   assets/chime_<signal>.mp3
"""

from __future__ import annotations

import math
import struct
from pathlib import Path

from pydub import AudioSegment  # pyright: ignore[reportMissingImports]

ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
SAMPLE_RATE = 44100
CHANNELS = 1
SAMPLE_WIDTH = 2  # 16-bit


def _sine_wave(
    freq_hz: float,
    duration_ms: int,
    volume_db: float = -10.0,
) -> AudioSegment:
    """Generate a pure sine wave as an AudioSegment."""
    n_samples = int(SAMPLE_RATE * duration_ms / 1000)
    max_val = 2 ** (SAMPLE_WIDTH * 8 - 1) - 1

    # Apply volume (dBFS)
    amplitude = max_val * (10 ** (volume_db / 20))

    raw = b"".join(
        struct.pack(
            "<h",
            int(amplitude * math.sin(2 * math.pi * freq_hz * i / SAMPLE_RATE)),
        )
        for i in range(n_samples)
    )

    seg: AudioSegment = AudioSegment(  # pyright: ignore[reportUnknownMemberType]
        data=raw,
        sample_width=SAMPLE_WIDTH,
        frame_rate=SAMPLE_RATE,
        channels=CHANNELS,
    )
    return seg


def _fade(
    seg: AudioSegment,
    fade_in_ms: int = 15,
    fade_out_ms: int = 50,
) -> AudioSegment:
    """Apply fade in/out to avoid clicks."""
    result: AudioSegment = seg.fade_in(fade_in_ms).fade_out(fade_out_ms)  # pyright: ignore[reportUnknownMemberType]
    return result


# Note frequencies (Hz)
C4 = 261.63
E4 = 329.63
G4 = 392.00
A5 = 880.00
E5 = 659.25


def chime_done() -> AudioSegment:
    """Warm resolution chord: C4 → E4 → G4."""
    note1 = _fade(_sine_wave(C4, 200, volume_db=-12.0))
    note2 = _fade(_sine_wave(E4, 200, volume_db=-11.0))
    note3 = _fade(_sine_wave(G4, 350, volume_db=-10.0))
    gap = AudioSegment.silent(duration=40)  # pyright: ignore[reportUnknownMemberType]
    result: AudioSegment = note1 + gap + note2 + gap + note3  # pyright: ignore[reportUnknownMemberType]
    return result


def chime_prompt() -> AudioSegment:
    """Gentle attention ping: A5 → E5."""
    note1 = _fade(_sine_wave(A5, 150, volume_db=-14.0), fade_in_ms=5, fade_out_ms=80)
    note2 = _fade(_sine_wave(E5, 250, volume_db=-12.0), fade_in_ms=5, fade_out_ms=120)
    gap = AudioSegment.silent(duration=60)  # pyright: ignore[reportUnknownMemberType]
    result: AudioSegment = note1 + gap + note2  # pyright: ignore[reportUnknownMemberType]
    return result


CHIMES = {
    "chime_done": chime_done,
    "chime_prompt": chime_prompt,
}


def main() -> None:
    ASSETS_DIR.mkdir(exist_ok=True)
    count = 0

    for name, generator in CHIMES.items():
        audio = generator()

        path = ASSETS_DIR / f"{name}.mp3"
        audio.export(path, format="mp3")  # pyright: ignore[reportUnknownMemberType]
        print(f"  {path.name} ({path.stat().st_size:,} bytes)")
        count += 1

    print(f"\nGenerated {count} chimes in {ASSETS_DIR}")


if __name__ == "__main__":
    main()
