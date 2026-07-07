"""Tests for the Program entity: each Z transition's guard and successor."""

from __future__ import annotations

from collections.abc import Callable
from typing import final

import pytest

from punt_vox.voxd.programs import (
    COMPLETE,
    AdvanceResult,
    Format,
    FrozenParts,
    Mode,
    Part,
    PlaybackPolicy,
    PlaybackStatus,
    Program,
    ProgramState,
    Reason,
)

PartFactory = Callable[[int], Part]
PoolFactory = Callable[..., frozenset[Part]]


def _generating_with_failed(
    policy: PlaybackPolicy, failed: Part, reason: Reason
) -> Program:
    """Build a generating_first Program that already carries a failed Part.

    Reachable via recover-from-empty, which keeps ``failed_parts``. Used to
    exercise the defensive "already failed" guards on the first-track ops.
    """
    state = ProgramState(
        fmt=Format.PLAYLIST,
        pool=frozenset(),
        failed_parts=FrozenParts.empty().with_failure(failed, reason),
        playing=None,
        last_played=None,
        mode=Mode.GENERATING_FIRST,
        filling=True,
        attempts=0,
        last_error=None,
    )
    return Program(state, policy)


@final
class CompletePolicy:
    """A policy that signals end-of-list -- illegal for a playlist."""

    def next_part(self, pool: tuple[Part, ...], playing: Part | None) -> AdvanceResult:
        return COMPLETE


def _new(policy: PlaybackPolicy) -> Program:
    return Program(ProgramState.initial(), policy)


def _generating(policy: PlaybackPolicy) -> Program:
    prog = _new(policy)
    prog.turn_on()
    return prog


def _filling(policy: PlaybackPolicy, mk: PartFactory) -> Program:
    prog = _generating(policy)
    prog.first_track_ok(mk(1))
    prog.fill_ok(mk(2))
    return prog


# -- generation path --------------------------------------------------------


class TestTurnOn:
    def test_empty_pool_generates_first(self, policy: PlaybackPolicy) -> None:
        prog = _new(policy)
        prog.turn_on()  # mutator returns None (enforced by the -> None signature)
        assert prog.mode is Mode.GENERATING_FIRST

    def test_partial_pool_plays_and_fills(
        self, policy: PlaybackPolicy, pool_of: PoolFactory
    ) -> None:
        prog = Program(ProgramState.restored(Format.PLAYLIST, pool_of(1, 2)), policy)
        prog.turn_on()
        assert prog.mode is Mode.PLAYING_FILLING
        assert prog.state.filling is True

    def test_full_pool_rotates(
        self, policy: PlaybackPolicy, pool_of: PoolFactory
    ) -> None:
        full = pool_of(*range(1, 13))
        prog = Program(ProgramState.restored(Format.PLAYLIST, full), policy)
        prog.turn_on()
        assert prog.mode is Mode.PLAYING_ROTATING

    def test_requires_off(self, policy: PlaybackPolicy) -> None:
        prog = _generating(policy)
        with pytest.raises(ValueError, match="turn_on requires mode off"):
            prog.turn_on()


class TestFirstTrack:
    def test_ok_starts_playing(self, policy: PlaybackPolicy, mk: PartFactory) -> None:
        prog = _generating(policy)
        prog.first_track_ok(mk(1))
        assert prog.mode is Mode.PLAYING_FILLING
        assert prog.playing == mk(1)

    def test_ok_requires_generating(
        self, policy: PlaybackPolicy, mk: PartFactory
    ) -> None:
        prog = _filling(policy, mk)
        with pytest.raises(ValueError, match="generating_first"):
            prog.first_track_ok(mk(3))

    def test_bad_prompt_fails_observably(
        self, policy: PlaybackPolicy, mk: PartFactory, reason: Reason
    ) -> None:
        prog = _generating(policy)
        prog.first_track_bad_prompt(mk(1), reason)
        assert prog.mode is Mode.FAILED
        assert prog.state.last_error == reason
        assert mk(1) in prog.failed_parts

    def test_bad_prompt_requires_generating(
        self, policy: PlaybackPolicy, mk: PartFactory, reason: Reason
    ) -> None:
        prog = _filling(policy, mk)
        with pytest.raises(ValueError, match="generating_first"):
            prog.first_track_bad_prompt(mk(3), reason)

    def test_ok_rejects_already_failed_part(
        self, policy: PlaybackPolicy, mk: PartFactory, reason: Reason
    ) -> None:
        prog = _generating_with_failed(policy, mk(1), reason)
        with pytest.raises(ValueError, match="already recorded as failed"):
            prog.first_track_ok(mk(1))

    def test_bad_prompt_rejects_already_failed_part(
        self, policy: PlaybackPolicy, mk: PartFactory, reason: Reason
    ) -> None:
        prog = _generating_with_failed(policy, mk(1), reason)
        with pytest.raises(ValueError, match="already recorded as failed"):
            prog.first_track_bad_prompt(mk(1), reason)

    def test_transient_backs_off(self, policy: PlaybackPolicy, reason: Reason) -> None:
        prog = _generating(policy)
        prog.first_track_transient(reason)
        assert prog.mode is Mode.RETRYING
        assert prog.state.attempts == 1

    def test_transient_requires_generating(
        self, policy: PlaybackPolicy, mk: PartFactory, reason: Reason
    ) -> None:
        prog = _filling(policy, mk)
        with pytest.raises(ValueError, match="generating_first"):
            prog.first_track_transient(reason)


class TestFill:
    def test_ok_grows_the_pool(self, policy: PlaybackPolicy, mk: PartFactory) -> None:
        prog = _filling(policy, mk)
        prog.fill_ok(mk(3))
        assert len(prog.pool) == 3

    def test_ok_stops_at_full(self, policy: PlaybackPolicy, mk: PartFactory) -> None:
        prog = _generating(policy)
        prog.first_track_ok(mk(1))
        for i in range(2, 13):
            prog.fill_ok(mk(i))
        assert prog.mode is Mode.PLAYING_ROTATING
        assert prog.state.filling is False

    def test_ok_requires_active_fill(
        self, policy: PlaybackPolicy, mk: PartFactory, reason: Reason
    ) -> None:
        prog = _filling(policy, mk)
        prog.fill_transient(reason)  # pauses the fill -> retrying
        with pytest.raises(ValueError, match="active fill"):
            prog.fill_ok(mk(3))

    def test_ok_rejects_duplicate(
        self, policy: PlaybackPolicy, mk: PartFactory
    ) -> None:
        prog = _filling(policy, mk)
        with pytest.raises(ValueError, match="already in pool"):
            prog.fill_ok(mk(1))

    def test_ok_rejects_failed_part(
        self, policy: PlaybackPolicy, mk: PartFactory, reason: Reason
    ) -> None:
        prog = _filling(policy, mk)
        prog.fill_bad_part(mk(9), reason)
        with pytest.raises(ValueError, match="already recorded as failed"):
            prog.fill_ok(mk(9))

    def test_bad_part_records_and_plays_on(
        self, policy: PlaybackPolicy, mk: PartFactory, reason: Reason
    ) -> None:
        prog = _filling(policy, mk)
        prog.fill_bad_part(mk(9), reason)
        assert prog.mode is Mode.PLAYING_FILLING
        assert prog.state.last_error is None  # program stays healthy
        assert mk(9) in prog.failed_parts

    def test_bad_part_requires_active_fill(
        self, policy: PlaybackPolicy, mk: PartFactory, reason: Reason
    ) -> None:
        prog = _generating(policy)
        with pytest.raises(ValueError, match="active fill"):
            prog.fill_bad_part(mk(9), reason)

    def test_bad_part_rejects_pool_member(
        self, policy: PlaybackPolicy, mk: PartFactory, reason: Reason
    ) -> None:
        prog = _filling(policy, mk)
        with pytest.raises(ValueError, match="already in pool"):
            prog.fill_bad_part(mk(1), reason)

    def test_bad_part_rejects_already_failed(
        self, policy: PlaybackPolicy, mk: PartFactory, reason: Reason
    ) -> None:
        prog = _filling(policy, mk)
        prog.fill_bad_part(mk(9), reason)
        with pytest.raises(ValueError, match="already recorded as failed"):
            prog.fill_bad_part(mk(9), reason)

    def test_transient_pauses_generation(
        self, policy: PlaybackPolicy, mk: PartFactory, reason: Reason
    ) -> None:
        prog = _filling(policy, mk)
        prog.fill_transient(reason)
        assert prog.mode is Mode.RETRYING
        assert prog.state.filling is False
        assert len(prog.pool) == 2  # playback continues over the existing pool

    def test_transient_requires_active_fill(
        self, policy: PlaybackPolicy, reason: Reason
    ) -> None:
        prog = _generating(policy)
        with pytest.raises(ValueError, match="active fill"):
            prog.fill_transient(reason)


class TestRetry:
    def _retrying(self, policy: PlaybackPolicy, reason: Reason) -> Program:
        prog = _generating(policy)
        prog.first_track_transient(reason)
        return prog

    def test_fails_increments(self, policy: PlaybackPolicy, reason: Reason) -> None:
        prog = self._retrying(policy, reason)
        prog.retry_fails(reason)
        assert prog.state.attempts == 2

    def test_fails_requires_retrying(
        self, policy: PlaybackPolicy, reason: Reason
    ) -> None:
        prog = _generating(policy)
        with pytest.raises(ValueError, match="retrying"):
            prog.retry_fails(reason)

    def test_fails_requires_below_cap(
        self, policy: PlaybackPolicy, reason: Reason
    ) -> None:
        prog = self._retrying(policy, reason)
        for _ in range(4):
            prog.retry_fails(reason)
        assert prog.state.attempts == 5
        with pytest.raises(ValueError, match="below the cap"):
            prog.retry_fails(reason)

    def test_exhausted_fails_program(
        self, policy: PlaybackPolicy, reason: Reason
    ) -> None:
        prog = self._retrying(policy, reason)
        for _ in range(4):
            prog.retry_fails(reason)
        prog.retry_exhausted(reason)
        assert prog.mode is Mode.FAILED
        assert prog.state.last_error == reason

    def test_exhausted_requires_retrying(
        self, policy: PlaybackPolicy, reason: Reason
    ) -> None:
        prog = _generating(policy)
        with pytest.raises(ValueError, match="retrying"):
            prog.retry_exhausted(reason)

    def test_exhausted_requires_cap(
        self, policy: PlaybackPolicy, reason: Reason
    ) -> None:
        prog = self._retrying(policy, reason)
        with pytest.raises(ValueError, match="at the cap"):
            prog.retry_exhausted(reason)

    def test_exhausted_requires_empty_pool(
        self, policy: PlaybackPolicy, mk: PartFactory, reason: Reason
    ) -> None:
        prog = _filling(policy, mk)
        prog.fill_transient(reason)  # retrying with a non-empty pool
        for _ in range(4):
            prog.retry_fails(reason)
        with pytest.raises(ValueError, match="empty pool"):
            prog.retry_exhausted(reason)

    def test_capped_self_loops_on_nonempty_pool(
        self, policy: PlaybackPolicy, mk: PartFactory, reason: Reason
    ) -> None:
        prog = _filling(policy, mk)
        playing = prog.playing
        prog.fill_transient(reason)  # retrying with a non-empty pool
        for _ in range(4):  # climb to the cap
            prog.retry_fails(reason)
        assert prog.state.attempts == 5
        prog.retry_capped(Reason("still transient"))
        assert prog.mode is Mode.RETRYING  # self-loop, not failed
        assert prog.state.attempts == 5  # pinned at the cap
        assert prog.playing == playing  # playback untouched
        assert prog.state.last_error == Reason("still transient")  # advisory refreshed

    def test_capped_requires_retrying(
        self, policy: PlaybackPolicy, reason: Reason
    ) -> None:
        prog = _generating(policy)
        with pytest.raises(ValueError, match="retrying"):
            prog.retry_capped(reason)

    def test_capped_requires_cap(self, policy: PlaybackPolicy, reason: Reason) -> None:
        prog = self._retrying(policy, reason)  # attempts 1, below the cap
        with pytest.raises(ValueError, match="at the cap"):
            prog.retry_capped(reason)

    def test_capped_requires_nonempty_pool(
        self, policy: PlaybackPolicy, reason: Reason
    ) -> None:
        prog = self._retrying(policy, reason)  # empty pool
        for _ in range(4):  # climb to the cap on an empty pool
            prog.retry_fails(reason)
        with pytest.raises(ValueError, match="non-empty pool"):
            prog.retry_capped(reason)

    def test_recover_from_empty_resumes_generation(
        self, policy: PlaybackPolicy, reason: Reason
    ) -> None:
        prog = self._retrying(policy, reason)
        prog.recover()
        assert prog.mode is Mode.GENERATING_FIRST

    def test_recover_from_pool_resumes_playback(
        self, policy: PlaybackPolicy, mk: PartFactory, reason: Reason
    ) -> None:
        prog = _filling(policy, mk)
        playing = prog.playing
        prog.fill_transient(reason)
        prog.recover()
        assert prog.mode is Mode.PLAYING_FILLING
        assert prog.playing == playing  # playback intact
        assert prog.state.filling is True

    def test_recover_requires_retrying(self, policy: PlaybackPolicy) -> None:
        prog = _generating(policy)
        with pytest.raises(ValueError, match="retrying"):
            prog.recover()


class TestVibeAndOff:
    def test_vibe_change_retunes(
        self, policy: PlaybackPolicy, mk: PartFactory, pool_of: PoolFactory
    ) -> None:
        prog = _filling(policy, mk)
        prog.vibe_style_change(pool_of(5, 6, 7))
        assert prog.mode is Mode.PLAYING_FILLING
        assert {p.index for p in prog.pool} == {5, 6, 7}

    def test_vibe_change_requires_active(
        self, policy: PlaybackPolicy, pool_of: PoolFactory
    ) -> None:
        prog = _new(policy)
        with pytest.raises(ValueError, match="active program"):
            prog.vibe_style_change(pool_of(1))

    def test_turn_off_keeps_pool(self, policy: PlaybackPolicy, mk: PartFactory) -> None:
        prog = _filling(policy, mk)
        prog.turn_off()
        assert prog.mode is Mode.OFF
        assert len(prog.pool) == 2  # ready pool remains on disk

    def test_turn_off_requires_active(self, policy: PlaybackPolicy) -> None:
        prog = _new(policy)
        with pytest.raises(ValueError, match="active program"):
            prog.turn_off()


# -- consume path -----------------------------------------------------------


class TestConsume:
    def test_rotate_advances(self, rotating: Program) -> None:
        before = rotating.playing
        rotating.rotate()
        assert rotating.playing != before
        assert rotating.state.last_played == before

    def test_rotate_requires_playing_mode(self, policy: PlaybackPolicy) -> None:
        prog = _generating(policy)  # generating_first has no cursor
        with pytest.raises(ValueError, match="playing mode"):
            prog.rotate()

    def test_rotate_requires_non_empty_pool(
        self, policy: PlaybackPolicy, reason: Reason
    ) -> None:
        prog = _generating(policy)
        prog.first_track_transient(reason)  # retrying with an empty pool
        with pytest.raises(ValueError, match="non-empty pool"):
            prog.rotate()

    def test_rotate_rejects_complete_signal(self, mk: PartFactory) -> None:
        prog = Program(ProgramState.initial(), CompletePolicy())
        prog.turn_on()
        prog.first_track_ok(mk(1))
        with pytest.raises(AssertionError, match="COMPLETE"):
            prog.rotate()

    def test_play_part_allows_replay(
        self, policy: PlaybackPolicy, mk: PartFactory
    ) -> None:
        prog = _filling(policy, mk)
        current = prog.playing
        assert current is not None
        prog.play_part(current)  # explicit index -> no anti-repeat
        assert prog.playing == current
        assert prog.state.last_played == current

    def test_play_part_requires_playing_mode(
        self, policy: PlaybackPolicy, mk: PartFactory
    ) -> None:
        prog = _generating(policy)
        with pytest.raises(ValueError, match="playing mode"):
            prog.play_part(mk(1))

    def test_play_part_rejects_absent_target(
        self, policy: PlaybackPolicy, mk: PartFactory
    ) -> None:
        prog = _filling(policy, mk)
        with pytest.raises(ValueError, match="ready Part"):
            prog.play_part(mk(99))

    def test_start_from_disk_partial_no_fill(
        self, policy: PlaybackPolicy, mk: PartFactory, pool_of: PoolFactory
    ) -> None:
        prog = Program(ProgramState.restored(Format.PLAYLIST, pool_of(1, 2, 3)), policy)
        prog.start_from_disk(mk(2))
        assert prog.mode is Mode.PLAYING_FILLING
        assert prog.playing == mk(2)
        assert prog.state.filling is False  # cold start does not generate

    def test_start_from_disk_full_rotates(
        self, policy: PlaybackPolicy, mk: PartFactory, pool_of: PoolFactory
    ) -> None:
        prog = Program(
            ProgramState.restored(Format.PLAYLIST, pool_of(*range(1, 13))), policy
        )
        prog.start_from_disk(mk(5))
        assert prog.mode is Mode.PLAYING_ROTATING

    def test_start_from_disk_requires_off(
        self, policy: PlaybackPolicy, mk: PartFactory
    ) -> None:
        prog = _filling(policy, mk)
        with pytest.raises(ValueError, match="mode off"):
            prog.start_from_disk(mk(1))

    def test_start_from_disk_rejects_absent_target(
        self, policy: PlaybackPolicy, mk: PartFactory, pool_of: PoolFactory
    ) -> None:
        prog = Program(ProgramState.restored(Format.PLAYLIST, pool_of(1, 2)), policy)
        with pytest.raises(ValueError, match="ready Part"):
            prog.start_from_disk(mk(99))


# -- observation ------------------------------------------------------------


class TestObservation:
    def test_status_is_coarse_projection(self, rotating: Program) -> None:
        assert rotating.status is PlaybackStatus.PLAYING

    def test_pool_is_sorted(self, policy: PlaybackPolicy, mk: PartFactory) -> None:
        prog = _generating(policy)
        prog.first_track_ok(mk(1))
        prog.fill_ok(mk(3))
        prog.fill_ok(mk(2))
        assert [p.index for p in prog.pool] == [1, 2, 3]

    def test_state_is_exposed(self, rotating: Program) -> None:
        assert isinstance(rotating.state, ProgramState)
