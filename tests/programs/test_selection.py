"""Tests for the ``Selection`` and ``SelectedPart`` value objects."""

from __future__ import annotations

from punt_vox.voxd.programs.part import Part
from punt_vox.voxd.programs.selection import SelectedPart, Selection


class TestSelectedPart:
    def test_playable_is_namespaced_by_locator(self) -> None:
        selected = SelectedPart(Part("001.mp3", 1), "trance--calm-a3f1c9")
        assert selected.playable.identity == "trance--calm-a3f1c9/001.mp3"

    def test_colliding_filenames_stay_distinct_across_albums(self) -> None:
        left = SelectedPart(Part("001.mp3", 1), "album-a")
        right = SelectedPart(Part("001.mp3", 1), "album-b")
        assert left.playable != right.playable


class TestSelectionFromAlbums:
    def test_union_spans_two_albums(self) -> None:
        selection = Selection.from_albums(
            [
                ("album-a", (Part("001.mp3", 1), Part("002.mp3", 2))),
                ("album-b", (Part("001.mp3", 1),)),
            ]
        )
        assert len(selection) == 3
        assert bool(selection) is True

    def test_playable_pool_is_all_distinct(self) -> None:
        selection = Selection.from_albums(
            [
                ("album-a", (Part("001.mp3", 1),)),
                ("album-b", (Part("001.mp3", 1),)),
            ]
        )
        pool = selection.playable_pool()
        assert len(pool) == 2
        assert len(set(pool)) == 2

    def test_empty_selection_is_falsey(self) -> None:
        selection = Selection.from_albums([])
        assert len(selection) == 0
        assert bool(selection) is False


class TestSelectionValueSemantics:
    def test_iter_yields_selected_parts(self) -> None:
        selection = Selection.from_albums([("a", (Part("001.mp3", 1),))])
        assert list(selection) == [SelectedPart(Part("001.mp3", 1), "a")]

    def test_equality_and_hash(self) -> None:
        a = Selection.from_albums([("a", (Part("001.mp3", 1),))])
        b = Selection.from_albums([("a", (Part("001.mp3", 1),))])
        assert a == b
        assert hash(a) == hash(b)

    def test_not_equal_to_foreign_type(self) -> None:
        assert Selection.from_albums([]) != "selection"

    def test_repr_counts_parts(self) -> None:
        selection = Selection.from_albums([("a", (Part("001.mp3", 1),))])
        assert repr(selection) == "Selection(parts=1)"
