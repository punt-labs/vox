"""Tests for the read-only music re-pool hint (src/punt_vox/music_hint.py)."""

from __future__ import annotations

import pytest

from punt_vox.music_hint import MusicHint
from punt_vox.types_programs.format import Format
from punt_vox.types_programs.mode import Mode
from punt_vox.types_programs.status import ProgramStatus
from punt_vox.types_programs.status_views import GenerationStatus


def _status(mode: Mode) -> ProgramStatus:
    """Return a status in *mode* with an idle generation surface."""
    return ProgramStatus(
        format=Format.PLAYLIST,
        mode=mode,
        generation=GenerationStatus(filling=False, attempts=0),
    )


def _playing() -> ProgramStatus:
    """Return a status whose Program is genuinely playing."""
    return ProgramStatus.radio(None, None)


def _off() -> ProgramStatus:
    """Return the idle status (mode == off)."""
    return ProgramStatus.idle()


class TestMusicHint:
    """The hint fires only while playing and names the current style."""

    def test_no_hint_when_music_off(self) -> None:
        assert MusicHint.for_status(_off(), "relaxing", "flamenco") is None

    @pytest.mark.parametrize(
        "mode",
        [Mode.OFF, Mode.GENERATING_FIRST, Mode.RETRYING, Mode.FAILED],
    )
    def test_no_hint_when_not_audible(self, mode: Mode) -> None:
        # A FAILED/RETRYING/generating Program is not "playing" -- a re-pool
        # directive would be false, so no hint fires.
        assert MusicHint.for_status(_status(mode), "relaxing", "flamenco") is None

    @pytest.mark.parametrize("mode", [Mode.PLAYING_FILLING, Mode.PLAYING_ROTATING])
    def test_hint_when_audible(self, mode: Mode) -> None:
        assert MusicHint.for_status(_status(mode), "relaxing", "flamenco") is not None

    def test_hint_when_playing_names_style_and_mood(self) -> None:
        hint = MusicHint.for_status(_playing(), "relaxing", "flamenco")
        assert hint is not None
        assert "style=flamenco" in hint.directive
        assert "flamenco x relaxing" in hint.directive
        assert hint.directive.endswith("Do it now.")

    def test_directive_example_is_unambiguous_and_executable(self) -> None:
        # The agent executes the rendered call, so the example must be accurate:
        # mode is a quoted string and variations reads as 12 authored prompts,
        # never a one-element list holding the integer 12.
        styled = MusicHint.for_status(_playing(), "relaxing", "flamenco")
        assert styled is not None
        assert 'mode="on"' in styled.directive
        assert "variations=[12]" not in styled.directive
        assert "variations=[<12 genre-mood prompts>]" in styled.directive
        assert 'style="flamenco"' in styled.directive

    def test_music_state_is_observable(self) -> None:
        hint = MusicHint.for_status(_playing(), "relaxing", "flamenco")
        assert hint is not None
        assert hint.music_state() == {"playing": True, "style": "flamenco"}

    def test_style_property(self) -> None:
        hint = MusicHint.for_status(_playing(), "x", "techno")
        assert hint is not None
        assert hint.style == "techno"

    def test_cleared_mood_uses_placeholder(self) -> None:
        hint = MusicHint.for_status(_playing(), None, "flamenco")
        assert hint is not None
        assert "the current session mood" in hint.directive

    def test_no_hint_when_style_unknown(self) -> None:
        # A known genre is what a re-pool preserves. With no style to name, a
        # directive would let the daemon default to ambient and silently switch
        # genres -- so no hint fires. The mood still updates; music keeps playing.
        assert MusicHint.for_status(_playing(), "relaxing", None) is None
