#!/usr/bin/env python3
"""Generate per-signal chime MP3 assets with mood variants.

Each chime is a short (0.5-1.2s) synthesized tone designed to be
instantly distinguishable by ear:

- done:            warm resolution chord (C4→E4→G4)
- prompt:          gentle attention ping (A5→E5)
- tests_pass:      bright ascending two-note (C5→G5)
- tests_fail:      low descending two-note (G3→C3)
- lint_pass:       crisp single high ping (E6)
- lint_fail:       dull low thud (A2)
- git_push_ok:     triumphant three-note arpeggio (C5→E5→G5)
- merge_conflict:  dissonant two-tone alert (C4+C#4)

Mood variants (bright/dark) are pitch-shifted ±3 semitones from the
neutral originals.

Requires: pydub (already a project dependency).
Output:   assets/chime_<signal>.mp3           (neutral)
          assets/chime_<signal>_bright.mp3    (+3 semitones)
          assets/chime_<signal>_dark.mp3      (-3 semitones)
"""

from __future__ import annotations

import io
import math
import struct
from pathlib import Path

from pydub import AudioSegment  # type: ignore[import-untyped]

ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
SAMPLE_RATE = 44100
CHANNELS = 1
SAMPLE_WIDTH = 2  # 16-bit


def _sine_wave(  # type: ignore[return]
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


def _fade(  # type: ignore[return]
    seg: AudioSegment,
    fade_in_ms: int = 15,
    fade_out_ms: int = 50,
) -> AudioSegment:
    """Apply fade in/out to avoid clicks."""
    result: AudioSegment = seg.fade_in(fade_in_ms).fade_out(fade_out_ms)  # pyright: ignore[reportUnknownMemberType]
    return result


# Note frequencies (Hz)
C3 = 130.81
G3 = 196.00
A2 = 110.00
C4 = 261.63
CS4 = 277.18  # C#4
E4 = 329.63
G4 = 392.00
A5 = 880.00
C5 = 523.25
E5 = 659.25
G5 = 783.99
E6 = 1318.51

# Mood variant pitch shifts (semitones)
MOOD_SHIFTS: dict[str, int] = {
    "bright": 3,
    "dark": -3,
}


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
    result: AudioSegment = _fade(
        _sine_wave(E6, 150, volume_db=-14.0),
        fade_in_ms=5,
        fade_out_ms=100,
    )
    return result


def chime_lint_fail() -> AudioSegment:  # type: ignore[return]
    """Dull low thud: A2."""
    result: AudioSegment = _fade(
        _sine_wave(A2, 350, volume_db=-6.0),
        fade_in_ms=10,
        fade_out_ms=200,
    )
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


def chime_done() -> AudioSegment:  # type: ignore[return]
    """Warm resolution chord: C4 → E4 → G4."""
    note1 = _fade(_sine_wave(C4, 200, volume_db=-12.0))
    note2 = _fade(_sine_wave(E4, 200, volume_db=-11.0))
    note3 = _fade(_sine_wave(G4, 350, volume_db=-10.0))
    gap = AudioSegment.silent(duration=40)  # pyright: ignore[reportUnknownMemberType]
    result: AudioSegment = note1 + gap + note2 + gap + note3  # pyright: ignore[reportUnknownMemberType]
    return result


def chime_prompt() -> AudioSegment:  # type: ignore[return]
    """Gentle attention ping: A5 → E5."""
    note1 = _fade(_sine_wave(A5, 150, volume_db=-14.0), fade_in_ms=5, fade_out_ms=80)
    note2 = _fade(_sine_wave(E5, 250, volume_db=-12.0), fade_in_ms=5, fade_out_ms=120)
    gap = AudioSegment.silent(duration=60)  # pyright: ignore[reportUnknownMemberType]
    result: AudioSegment = note1 + gap + note2  # pyright: ignore[reportUnknownMemberType]
    return result


def _pitch_shift(seg: AudioSegment, semitones: int) -> AudioSegment:  # type: ignore[return]
    """Shift pitch by *semitones* via sample rate manipulation.

    Exports to MP3 at the altered frame rate, then reimports at the
    original rate. This changes pitch without changing duration
    (though duration shifts slightly — acceptable for short chimes).
    """
    factor = 2 ** (semitones / 12)
    original_rate: int = seg.frame_rate  # pyright: ignore[reportUnknownMemberType]
    shifted_rate = int(original_rate * factor)

    # Change frame rate (speeds up / slows down)
    shifted: AudioSegment = seg._spawn(  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue]
        seg.raw_data,  # pyright: ignore[reportUnknownMemberType]
        overrides={"frame_rate": shifted_rate},
    )

    # Export and reimport at original rate to resample
    buf = io.BytesIO()
    shifted.export(buf, format="mp3")  # pyright: ignore[reportUnknownMemberType]
    buf.seek(0)
    result: AudioSegment = AudioSegment.from_mp3(buf)  # pyright: ignore[reportUnknownMemberType]
    return result


CHIMES = {
    "chime_done": chime_done,
    "chime_prompt": chime_prompt,
    "chime_tests_pass": chime_tests_pass,
    "chime_tests_fail": chime_tests_fail,
    "chime_lint_pass": chime_lint_pass,
    "chime_lint_fail": chime_lint_fail,
    "chime_git_push_ok": chime_git_push_ok,
    "chime_merge_conflict": chime_merge_conflict,
}


def main() -> None:
    ASSETS_DIR.mkdir(exist_ok=True)
    count = 0

    for name, generator in CHIMES.items():
        audio = generator()

        # Neutral (original)
        path = ASSETS_DIR / f"{name}.mp3"
        audio.export(path, format="mp3")  # pyright: ignore[reportUnknownMemberType]
        print(f"  {path.name} ({path.stat().st_size:,} bytes)")
        count += 1

        # Mood variants (bright, dark)
        for mood, semitones in MOOD_SHIFTS.items():
            shifted = _pitch_shift(audio, semitones)
            mood_path = ASSETS_DIR / f"{name}_{mood}.mp3"
            shifted.export(mood_path, format="mp3")  # pyright: ignore[reportUnknownMemberType]
            print(f"  {mood_path.name} ({mood_path.stat().st_size:,} bytes)")
            count += 1

    print(f"\nGenerated {count} chimes in {ASSETS_DIR}")


if __name__ == "__main__":
    main()
