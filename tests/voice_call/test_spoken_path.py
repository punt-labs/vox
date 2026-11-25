"""A spoken path names its last segment first and deepens one parent at a time."""

from __future__ import annotations

import pytest

from punt_vox.voxd.voice_call.spoken_path import SpokenPath


def test_speaks_the_last_segment_first() -> None:
    assert SpokenPath("/Users/jim/work/vox").spoken() == "vox"


def test_deepen_adds_one_parent_per_call() -> None:
    path = SpokenPath("/Users/jim/work/vox")
    assert path.deepen() is True
    assert path.spoken() == "work vox"
    assert path.deepen() is True
    assert path.spoken() == "jim work vox"


def test_deepen_stops_at_the_root() -> None:
    """Once every segment is spoken there is no parent left to add."""
    path = SpokenPath("/a/b")
    assert path.deepen() is True
    assert path.deepen() is False
    assert path.spoken() == "a b"


@pytest.mark.parametrize("project", ["vox", "/", ""])
def test_a_single_segment_or_bare_root_never_deepens(project: str) -> None:
    path = SpokenPath(project)
    before = path.spoken()
    assert path.deepen() is False
    assert path.spoken() == before


def test_segments_are_joined_by_spaces_never_slashes() -> None:
    path = SpokenPath("/one/two/three")
    while path.deepen():
        pass
    assert "/" not in path.spoken()
    assert path.spoken() == "one two three"
