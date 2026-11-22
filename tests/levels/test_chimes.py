"""Bundled chimes start at once, stay unclipped, and match each other in level."""

from __future__ import annotations

from pathlib import Path

import pytest

from levels import meter
from punt_vox.voxd.chimes import ChimeResolver

pytestmark = pytest.mark.levels

SIGNALS = ["done", "prompt", "acknowledge", "compact", "subagent", "farewell"]

# Measured on the bundled assets (ffmpeg ebur128): done -16.3 LUFS / -10.4 dBTP,
# 0.86 s; prompt -18.5 LUFS / -12.4 dBTP, 0.50 s; both sound within 1 ms.
LOUDNESS_LUFS = (-21.0, -13.0)
MAX_TRUE_PEAK_DBTP = -1.0
MAX_SPREAD_LU = 3.0
DURATION_S = (0.2, 1.5)
MAX_ONSET_S = 0.02


def chime(signal: str) -> Path:
    path = ChimeResolver().resolve(signal)
    assert path is not None, f"chime {signal!r} has no bundled asset"
    return path


def assert_chime(path: Path) -> meter.Loudness:
    length = meter.duration(path)
    assert DURATION_S[0] <= length <= DURATION_S[1], f"duration {length:.2f}s"
    onset = meter.leading_silence(path)
    assert onset <= MAX_ONSET_S, f"{onset * 1000:.0f} ms of silence before the chime"
    level = meter.loudness(path)
    assert level.true_peak_dbtp <= MAX_TRUE_PEAK_DBTP, (
        f"true peak {level.true_peak_dbtp} dBTP clips"
    )
    low, high = LOUDNESS_LUFS
    assert low <= level.integrated_lufs <= high, f"{level.integrated_lufs} LUFS"
    return level


@pytest.mark.parametrize("signal", SIGNALS)
def test_chime_signal(signal: str) -> None:
    level = assert_chime(chime(signal))
    print(f"chime {signal}: {level}")


def test_chimes_match_in_loudness() -> None:
    levels = [meter.loudness(chime(signal)).integrated_lufs for signal in SIGNALS]
    spread = max(levels) - min(levels)
    assert spread <= MAX_SPREAD_LU, f"chimes differ by {spread:.1f} LU"


@pytest.mark.parametrize(
    ("fault", "audio_filter", "message"),
    [
        ("late", "adelay=200:all=1", "silence before the chime"),
        ("quiet", "volume=-20dB", "LUFS"),
        ("clipped", "volume=15dB,alimiter=limit=1:level=false", "clips"),
        ("truncated", "atrim=0:0.1", "duration"),
    ],
)
def test_faulted_chime_is_rejected(
    fault: str, audio_filter: str, message: str, tmp_path: Path
) -> None:
    faulted = meter.transform(chime("done"), tmp_path / f"{fault}.wav", audio_filter)
    with pytest.raises(AssertionError, match=message):
        assert_chime(faulted)
