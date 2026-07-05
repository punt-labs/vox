"""Tests for ProgramState construction, activation, and successor building."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from punt_vox.voxd.programs import (
    Format,
    FrozenParts,
    Mode,
    Part,
    ProgramState,
    Reason,
)

PoolFactory = Callable[..., frozenset[Part]]


class TestConstruction:
    def test_initial_is_idle_and_empty(self) -> None:
        state = ProgramState.initial()
        assert state.mode is Mode.OFF
        assert state.format is Format.PLAYLIST
        assert state.pool == frozenset()
        assert state.playing is None
        assert state.last_played is None
        assert state.filling is False
        assert state.attempts == 0
        assert state.last_error is None
        assert len(state.failed_parts) == 0

    def test_restored_loads_a_saved_pool_idle(self, pool_of: PoolFactory) -> None:
        state = ProgramState.restored(Format.PLAYLIST, pool_of(1, 2, 3))
        assert state.mode is Mode.OFF
        assert len(state.pool) == 3

    def test_restored_rejects_overfull_pool(self, pool_of: PoolFactory) -> None:
        with pytest.raises(ValueError, match="S1"):
            ProgramState.restored(Format.PODCAST, pool_of(1, 2, 3, 4, 5, 6, 7))


class TestActivation:
    def test_empty_pool_activates_generating(self) -> None:
        act = ProgramState.initial().activation(frozenset())
        assert act.mode is Mode.GENERATING_FIRST
        assert act.filling is True
        assert act.playing is None

    def test_partial_pool_activates_filling(self, pool_of: PoolFactory) -> None:
        act = ProgramState.initial().activation(pool_of(2, 1, 3))
        assert act.mode is Mode.PLAYING_FILLING
        assert act.filling is True
        assert act.playing == Part("id001", 1)  # lowest index -> stable start

    def test_full_pool_activates_rotating(self, pool_of: PoolFactory) -> None:
        full = pool_of(*range(1, 13))
        act = ProgramState.initial().activation(full)
        assert act.mode is Mode.PLAYING_ROTATING
        assert act.filling is False
        assert act.playing == Part("id001", 1)


class TestOrdering:
    def test_ordered_pool_sorted_by_index(self, pool_of: PoolFactory) -> None:
        state = ProgramState.restored(Format.PLAYLIST, pool_of(3, 1, 2))
        assert [p.index for p in state.ordered_pool] == [1, 2, 3]


class TestWithUpdates:
    def _retrying(self, reason: Reason) -> ProgramState:
        return ProgramState.initial().with_updates(
            mode=Mode.RETRYING, attempts=1, last_error=reason
        )

    def test_unnamed_fields_carry_forward(self) -> None:
        state = self._retrying(Reason("boom"))
        same = state.with_updates()
        assert same.last_error == Reason("boom")
        assert same.attempts == 1

    def test_none_clears_an_optional(self) -> None:
        state = self._retrying(Reason("boom"))
        cleared = state.with_updates(last_error=None)
        assert cleared.last_error is None

    def test_format_is_never_changed(self, pool_of: PoolFactory) -> None:
        state = ProgramState.restored(Format.PODCAST, pool_of(1))
        assert state.with_updates(attempts=0).format is Format.PODCAST


class TestValueSemantics:
    def test_equal_states_from_different_paths(self) -> None:
        assert ProgramState.initial() == ProgramState.restored(
            Format.PLAYLIST, frozenset()
        )

    def test_equal_states_hash_equal(self) -> None:
        a = ProgramState.initial()
        b = ProgramState.restored(Format.PLAYLIST, frozenset())
        assert hash(a) == hash(b)

    def test_distinct_states_unequal(self, pool_of: PoolFactory) -> None:
        assert ProgramState.initial() != ProgramState.restored(
            Format.PLAYLIST, pool_of(1)
        )

    def test_not_equal_to_foreign_type(self) -> None:
        assert ProgramState.initial() != "off"

    def test_repr_names_the_mode(self) -> None:
        assert "off" in repr(ProgramState.initial())

    def test_hashable_as_set_member(self) -> None:
        assert len({ProgramState.initial(), ProgramState.initial()}) == 1


def test_failed_parts_default_is_empty() -> None:
    assert ProgramState.initial().failed_parts == FrozenParts.empty()
