"""Live ElevenLabs music is audible, unclipped, gapless, and sits under chimes."""

from __future__ import annotations

import asyncio
import math
import os
from pathlib import Path

import pytest

from levels import meter
from levels.transition import Transition
from punt_vox.providers.elevenlabs_music import ElevenLabsMusicProvider
from punt_vox.types_programs.prompts import PromptSet
from punt_vox.voxd.chimes import ChimeResolver
from punt_vox.voxd.programs.mpv.mpv_launcher import _MUSIC_VOLUME

pytestmark = [pytest.mark.levels, pytest.mark.integration]

TRACK_MS = 20_000
STYLES = [("lofi", "focused"), ("ambient", "calm")]

LOUDNESS_LUFS = (-30.0, -8.0)
MAX_TRUE_PEAK_DBTP = 0.0
MAX_LEADING_SILENCE_S = 1.0
MAX_INTERNAL_SILENCE_S = 2.0
MIN_CHIME_HEADROOM_LU = 3.0
# playback.py plays chimes at afplay -v 0.8 / ffplay -volume 80, both linear.
CHIME_GAIN_DB = 20 * math.log10(0.8)


@pytest.fixture(scope="module")
def tracks(tmp_path_factory: pytest.TempPathFactory) -> list[Path]:
    if not os.environ.get("ELEVENLABS_API_KEY"):
        reason = "levels prerequisite missing: ELEVENLABS_API_KEY"
        if os.environ.get("VOX_LEVELS_REQUIRE_LIVE") == "1":
            pytest.fail(reason)
        pytest.skip(reason)
    folder = tmp_path_factory.mktemp("music")
    provider = ElevenLabsMusicProvider()

    async def generate() -> list[Path]:
        return list(
            await asyncio.gather(
                *(
                    provider.generate_track(
                        PromptSet.fallback(style, mood).prompt_for(0),
                        TRACK_MS,
                        folder / f"{style}.mp3",
                    )
                    for style, mood in STYLES
                )
            )
        )

    return asyncio.run(generate())


@pytest.mark.parametrize("index", range(len(STYLES)))
def test_track_signal(tracks: list[Path], index: int) -> None:
    track = tracks[index]
    level = meter.loudness(track)
    print(f"music {STYLES[index]}: {level}")
    assert level.true_peak_dbtp <= MAX_TRUE_PEAK_DBTP, f"{level} clips"
    low, high = LOUDNESS_LUFS
    assert low <= level.integrated_lufs <= high, f"{level.integrated_lufs} LUFS"
    onset = meter.leading_silence(track)
    assert onset <= MAX_LEADING_SILENCE_S, f"{onset:.2f}s before the music starts"
    longest = max(
        (span.duration for span in meter.silences(track, 0.05) if span.start > 0.0),
        default=0.0,
    )
    assert longest <= MAX_INTERNAL_SILENCE_S, f"{longest:.2f}s of dead air"


def test_track_change_is_smooth(tracks: list[Path], tmp_path: Path) -> None:
    first, second = tracks
    change = Transition.between(first, second, tmp_path / "rendered.wav", _MUSIC_VOLUME)
    print(f"transition: gap {change.gap():.2f}s, jump {change.jump():.1f} LU")
    change.assert_smooth()


def test_chimes_sit_above_music(tracks: list[Path], tmp_path: Path) -> None:
    played = meter.render(tracks[:1], tmp_path / "played.wav", _MUSIC_VOLUME)
    music = meter.loudness(played).integrated_lufs
    for signal in ("done", "prompt"):
        path = ChimeResolver().resolve(signal)
        assert path is not None
        chime = meter.loudness(path).integrated_lufs + CHIME_GAIN_DB
        headroom = chime - music
        print(f"chime {signal} over music: {headroom:.1f} LU")
        assert headroom >= MIN_CHIME_HEADROOM_LU, (
            f"chime {signal} only {headroom:.1f} LU above music"
        )
