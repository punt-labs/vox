"""Tests for the ``PlaybackSource`` structural protocol.

The protocol is the source union the loop and channel animate. These tests pin
its structural surface -- a conforming source satisfies ``isinstance`` and a
missing member fails it -- so the Program/SelectionPlayback adapters that arrive
later are checked against a stable contract.
"""

from __future__ import annotations

from typing import final

from punt_vox.voxd.programs.part import Part
from punt_vox.voxd.programs.playback_source import PlaybackSource


@final
class _ConformingSource:
    """A minimal source with every ``PlaybackSource`` member."""

    __slots__ = ("_playing",)
    _playing: Part | None

    def __new__(cls, playing: Part | None) -> _ConformingSource:
        self = super().__new__(cls)
        self._playing = playing
        return self

    @property
    def playing(self) -> Part | None:
        return self._playing

    def rotate(self) -> None:
        return None

    @property
    def wants_generation(self) -> bool:
        return False

    @property
    def advances_on_end(self) -> bool:
        return self._playing is not None


@final
class _MissingMembers:
    """A source missing ``wants_generation``/``advances_on_end`` -- not conforming."""

    __slots__ = ()

    @property
    def playing(self) -> Part | None:
        return None

    def rotate(self) -> None:
        return None


class TestProtocolConformance:
    def test_conforming_source_is_instance(self) -> None:
        source = _ConformingSource(Part("001.mp3", 1))
        assert isinstance(source, PlaybackSource)

    def test_source_missing_members_is_not_instance(self) -> None:
        assert not isinstance(_MissingMembers(), PlaybackSource)

    def test_advances_on_end_tracks_the_cursor(self) -> None:
        assert _ConformingSource(Part("001.mp3", 1)).advances_on_end is True
        assert _ConformingSource(None).advances_on_end is False
