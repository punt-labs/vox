"""The transition oracle passes a seamless change and catches gaps and jumps."""

from __future__ import annotations

from pathlib import Path

import pytest

from levels import meter
from levels.transition import Transition
from punt_vox.voxd.programs.mpv.mpv_launcher import _MUSIC_VOLUME

pytestmark = pytest.mark.levels


def track(tmp_path: Path, name: str, gain_db: float, tail: float = 0.0) -> Path:
    path = meter.tone(tmp_path / f"{name}.wav", 6.0, gain_db)
    if tail:
        return meter.transform(
            path, tmp_path / f"{name}-tail.wav", f"apad=pad_dur={tail}"
        )
    return path


def transition(tmp_path: Path, first: Path, second: Path) -> Transition:
    return Transition.between(first, second, tmp_path / "rendered.wav", _MUSIC_VOLUME)


def test_matched_gapless_tracks_pass(tmp_path: Path) -> None:
    change = transition(tmp_path, track(tmp_path, "a", -12), track(tmp_path, "b", -13))
    change.assert_smooth()
    assert change.gap() == 0.0
    assert change.jump() <= 1.5


def test_dead_air_between_tracks_fails(tmp_path: Path) -> None:
    first = track(tmp_path, "a", -12, tail=2.0)
    change = transition(tmp_path, first, track(tmp_path, "b", -12))
    with pytest.raises(AssertionError, match="silence between tracks"):
        change.assert_smooth()


def test_loudness_jump_between_tracks_fails(tmp_path: Path) -> None:
    change = transition(tmp_path, track(tmp_path, "a", -22), track(tmp_path, "b", -6))
    with pytest.raises(AssertionError, match="loudness jump"):
        change.assert_smooth()


def test_render_applies_music_volume(tmp_path: Path) -> None:
    source = track(tmp_path, "a", -12)
    rendered = meter.render([source], tmp_path / "rendered.wav", _MUSIC_VOLUME)
    drop = (
        meter.loudness(source).integrated_lufs
        - meter.loudness(rendered).integrated_lufs
    )
    # mpv's volume is cubic: 60 plays at 0.6**3, about 13.3 dB down.
    assert 12.0 <= drop <= 14.5
