"""Tests for punt_vox.voxd.music.pool -- TrackPool selection value object."""

from __future__ import annotations

from pathlib import Path

import pytest

from punt_vox.voxd.music.pool import POOL_SIZE, TrackPool

__all__: list[str] = []


def _paths(count: int) -> tuple[Path, ...]:
    """Return ``count`` distinct fake track paths."""
    return tuple(Path(f"/music/track_{i:02d}.mp3") for i in range(count))


class TestIsFull:
    """TrackPool.is_full reflects the POOL_SIZE threshold."""

    def test_below_threshold_is_not_full(self) -> None:
        assert TrackPool.from_paths(_paths(POOL_SIZE - 1)).is_full is False

    def test_at_threshold_is_full(self) -> None:
        assert TrackPool.from_paths(_paths(POOL_SIZE)).is_full is True

    def test_above_threshold_is_full(self) -> None:
        assert TrackPool.from_paths(_paths(POOL_SIZE + 5)).is_full is True

    def test_empty_is_not_full(self) -> None:
        assert TrackPool.from_paths(()).is_full is False


class TestPickNext:
    """TrackPool.pick_next shuffles and avoids the just-played track."""

    def test_returns_a_pool_member(self) -> None:
        pool = TrackPool.from_paths(_paths(POOL_SIZE))
        chosen = pool.pick_next(None)
        assert chosen in set(_paths(POOL_SIZE))

    def test_never_returns_last(self) -> None:
        paths = _paths(POOL_SIZE)
        pool = TrackPool.from_paths(paths)
        last = paths[0]
        for _ in range(50):
            assert pool.pick_next(last) != last

    def test_last_absent_from_pool_allows_any(self) -> None:
        paths = _paths(POOL_SIZE)
        pool = TrackPool.from_paths(paths)
        chosen = pool.pick_next(Path("/music/not_in_pool.mp3"))
        assert chosen in set(paths)

    def test_single_track_pool_returns_it_even_as_last(self) -> None:
        only = Path("/music/solo.mp3")
        pool = TrackPool.from_paths((only,))
        assert pool.pick_next(only) == only

    def test_empty_pool_raises(self) -> None:
        pool = TrackPool.from_paths(())
        with pytest.raises(ValueError, match="empty pool"):
            pool.pick_next(None)
