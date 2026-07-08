"""Tests for the ``SelectionPlayback`` consume-only replay source.

Asserts the modeled Radio invariants by name: ``playing in selection``, no
immediate repeat when ``#selection >= 2`` (reusing ``RotatePolicy``), a singleton
replays, a Selection never generates, and an empty Selection is a caught boundary.
"""

from __future__ import annotations

from punt_vox.voxd.programs.part import Part
from punt_vox.voxd.programs.playback_source import PlaybackSource
from punt_vox.voxd.programs.rotate_policy import RotatePolicy
from punt_vox.voxd.programs.selection import Selection
from punt_vox.voxd.programs.selection_playback import SelectionPlayback


def _union() -> Selection:
    return Selection.from_albums(
        [
            ("album-a", (Part("001.mp3", 1), Part("002.mp3", 2))),
            ("album-b", (Part("001.mp3", 1), Part("002.mp3", 2))),
        ]
    )


class TestConstruction:
    def test_begins_at_first_track(self) -> None:
        selection = _union()
        source = SelectionPlayback(selection, RotatePolicy())
        assert source.playing == selection.playable_pool()[0]
        assert source.last_played is None

    def test_conforms_to_playback_source(self) -> None:
        source = SelectionPlayback(_union(), RotatePolicy())
        assert isinstance(source, PlaybackSource)


class TestRotate:
    def test_playing_stays_in_selection(self) -> None:
        selection = _union()
        source = SelectionPlayback(selection, RotatePolicy())
        pool = set(selection.playable_pool())
        for _ in range(10):
            source.rotate()
            assert source.playing in pool

    def test_no_immediate_repeat_when_two_or_more(self) -> None:
        source = SelectionPlayback(_union(), RotatePolicy())
        for _ in range(10):
            before = source.playing
            source.rotate()
            assert source.playing != before
            assert source.last_played == before

    def test_singleton_selection_replays(self) -> None:
        selection = Selection.from_albums([("album-a", (Part("001.mp3", 1),))])
        source = SelectionPlayback(selection, RotatePolicy())
        only = source.playing
        source.rotate()
        assert source.playing == only


class TestNeverGenerates:
    def test_wants_generation_is_false(self) -> None:
        assert SelectionPlayback(_union(), RotatePolicy()).wants_generation is False

    def test_is_playing_tracks_the_cursor(self) -> None:
        assert SelectionPlayback(_union(), RotatePolicy()).is_playing is True


class TestEmptyBoundary:
    def test_empty_selection_holds_no_cursor(self) -> None:
        source = SelectionPlayback(Selection.from_albums([]), RotatePolicy())
        assert source.playing is None
        assert source.is_playing is False

    def test_rotate_on_empty_is_a_no_op(self) -> None:
        source = SelectionPlayback(Selection.from_albums([]), RotatePolicy())
        source.rotate()
        assert source.playing is None
