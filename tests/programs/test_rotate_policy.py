"""Tests for the RotatePolicy playlist advance strategy."""

from __future__ import annotations

import pytest

from punt_vox.voxd.programs import Advance, Part
from punt_vox.voxd.programs.rotate_policy import RotatePolicy


def _parts(*indices: int) -> tuple[Part, ...]:
    return tuple(Part(f"id{i:03d}", i) for i in indices)


def test_avoids_immediate_repeat() -> None:
    policy = RotatePolicy()
    pool = _parts(1, 2, 3)
    playing = pool[0]
    for _ in range(50):
        result = policy.next_part(pool, playing)
        assert isinstance(result, Advance)
        assert result.part != playing


def test_single_part_replays() -> None:
    policy = RotatePolicy()
    (only,) = _parts(1)
    result = policy.next_part((only,), only)
    assert isinstance(result, Advance)
    assert result.part is only


def test_two_parts_always_swaps() -> None:
    policy = RotatePolicy()
    pool = _parts(1, 2)
    playing = pool[0]
    result = policy.next_part(pool, playing)
    assert isinstance(result, Advance)
    assert result.part == pool[1]


def test_no_playing_returns_a_pool_member() -> None:
    policy = RotatePolicy()
    pool = _parts(1, 2, 3)
    result = policy.next_part(pool, None)
    assert isinstance(result, Advance)
    assert result.part in pool


def test_empty_pool_raises() -> None:
    with pytest.raises(ValueError, match="empty pool"):
        RotatePolicy().next_part((), None)
