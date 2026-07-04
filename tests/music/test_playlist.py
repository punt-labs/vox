"""Tests for Playlist -- pool identity, fill control, and selection.

Playlist is driven directly here with an in-memory store; its collaboration
with the loop and scheduler is covered in test_loop / test_scheduler.
"""
# pyright: reportPrivateUsage=false

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from music.conftest import FakeTrackStore
from punt_vox.voxd.music.generator import TrackGenerator
from punt_vox.voxd.music.playlist import Playlist
from punt_vox.voxd.music.pool import POOL_SIZE

if TYPE_CHECKING:
    import pytest

__all__: list[str] = []

_CHOICE = "punt_vox.voxd.music.pool.secrets.choice"


def _playlist(store: FakeTrackStore) -> Playlist:
    return Playlist(TrackGenerator(store))


def _seed(store: FakeTrackStore, vibe: str, style: str, count: int) -> str:
    prefix = TrackGenerator.pool_prefix((vibe, style))
    for i in range(count):
        store.add(f"{prefix}{i:02d}")
    return prefix


def _first(seq: Sequence[Path]) -> Path:
    return seq[0]


class TestPoolIdentity:
    """retune / set_prefix point the playlist at a pool."""

    def test_retune_sets_vibe_style_and_prefix(self) -> None:
        store = FakeTrackStore()
        prefix = _seed(store, "calm", "jazz", 2)
        pl = _playlist(store)
        pl.retune(("calm", "[warm]"), "jazz")
        assert pl.vibe == ("calm", "[warm]")
        assert pl.style == "jazz"
        assert pl.select_first() in set(store.tracks_for(prefix))

    def test_retune_keeps_style_when_blank(self) -> None:
        pl = _playlist(FakeTrackStore())
        pl.retune(("calm", ""), "jazz")
        pl.retune(("bright", ""), "")  # blank style must not clear jazz
        assert pl.style == "jazz"

    def test_set_prefix_targets_a_pool_directly(self) -> None:
        store = FakeTrackStore()
        prefix = _seed(store, "calm", "jazz", 3)
        pl = _playlist(store)
        pl.set_prefix(prefix)
        assert not pl.is_empty


class TestSelection:
    """Selection avoids the just-played track; a lone track loops."""

    def test_is_empty_true_without_tracks(self) -> None:
        assert _playlist(FakeTrackStore()).is_empty

    def test_select_next_avoids_current(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(_CHOICE, _first)
        store = FakeTrackStore()
        prefix = _seed(store, "calm", "jazz", POOL_SIZE)
        pl = _playlist(store)
        pl.retune(("calm", ""), "jazz")
        current = store.path_for(f"{prefix}00")
        pl.mark_playing(current)
        assert pl.select_next() != current

    def test_single_track_pool_loops(self) -> None:
        store = FakeTrackStore()
        prefix = _seed(store, "calm", "jazz", 1)
        pl = _playlist(store)
        pl.retune(("calm", ""), "jazz")
        only = store.path_for(f"{prefix}00")
        pl.mark_playing(only)
        assert pl.select_next() == only

    def test_clear_current_resets_avoid_key(self) -> None:
        store = FakeTrackStore()
        prefix = _seed(store, "calm", "jazz", 2)
        pl = _playlist(store)
        pl.retune(("calm", ""), "jazz")
        pl.mark_playing(store.path_for(f"{prefix}00"))
        assert pl.current_track is not None
        pl.clear_current()
        assert pl.current_track is None


class TestFind:
    """find locates a saved track by name."""

    def test_find_existing(self) -> None:
        store = FakeTrackStore()
        track = store.add("my_focus")
        assert _playlist(store).find("my focus") == track

    def test_find_missing(self) -> None:
        assert _playlist(FakeTrackStore()).find("nope") is None
