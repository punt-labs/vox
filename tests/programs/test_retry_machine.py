"""Tests for RetryMachine -- each Z retrying/failed transition's guard and successor.

RetryMachine computes successors over a ProgramState in isolation from Program,
so these tests build the source state directly and assert on the returned state.
Program's own delegation is covered end-to-end in test_program.py; here the six
transitions -- including the capped self-loop and the empty-pool exhaustion guard
-- are exercised against the extracted surface.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from punt_vox.voxd.programs.format import MAX_RETRY, Format
from punt_vox.voxd.programs.identifiers import Reason
from punt_vox.voxd.programs.mode import Mode
from punt_vox.voxd.programs.part import FrozenParts, Part
from punt_vox.voxd.programs.retry_machine import RetryMachine
from punt_vox.voxd.programs.state import ProgramState

PartFactory = Callable[[int], Part]
PoolFactory = Callable[..., frozenset[Part]]


def _generating_first() -> ProgramState:
    """An empty-pool Program mid first-track generation (Z ``generatingFirst``)."""
    return ProgramState(
        fmt=Format.PLAYLIST,
        pool=frozenset(),
        failed_parts=FrozenParts.empty(),
        playing=None,
        last_played=None,
        mode=Mode.GENERATING_FIRST,
        filling=True,
        attempts=0,
        last_error=None,
    )


def _playing_filling(pool: frozenset[Part], playing: Part) -> ProgramState:
    """A partial pool playing with an active background fill (Z ``playingFilling``)."""
    return ProgramState(
        fmt=Format.PLAYLIST,
        pool=pool,
        failed_parts=FrozenParts.empty(),
        playing=playing,
        last_played=None,
        mode=Mode.PLAYING_FILLING,
        filling=True,
        attempts=0,
        last_error=None,
    )


def _retrying(
    *, pool: frozenset[Part], playing: Part | None, attempts: int
) -> ProgramState:
    """A backoff state (Z ``retrying``); ``pool = ∅ ⟺ playing = ∅`` by S15."""
    return ProgramState(
        fmt=Format.PLAYLIST,
        pool=pool,
        failed_parts=FrozenParts.empty(),
        playing=playing,
        last_played=None,
        mode=Mode.RETRYING,
        filling=False,
        attempts=attempts,
        last_error=Reason("prior transient"),
    )


class TestFirstTrackTransient:
    def test_backs_off_into_retrying(self, reason: Reason) -> None:
        after = RetryMachine(_generating_first()).first_track_transient(reason)
        assert after.mode is Mode.RETRYING
        assert after.attempts == 1
        assert after.pool == frozenset()
        assert after.playing is None
        assert after.filling is False
        assert after.last_error == reason

    def test_requires_generating_first(self, mk: PartFactory, reason: Reason) -> None:
        state = _playing_filling(frozenset({mk(1)}), mk(1))
        with pytest.raises(ValueError, match="generating_first"):
            RetryMachine(state).first_track_transient(reason)


class TestFillTransient:
    def test_pauses_the_fill_and_plays_on(
        self, mk: PartFactory, pool_of: PoolFactory, reason: Reason
    ) -> None:
        pool = pool_of(1, 2)
        after = RetryMachine(_playing_filling(pool, mk(1))).fill_transient(reason)
        assert after.mode is Mode.RETRYING
        assert after.filling is False
        assert after.attempts == 1
        assert after.pool == pool  # playback continues over the existing pool
        assert after.playing == mk(1)
        assert after.last_error == reason

    def test_requires_active_fill(self, reason: Reason) -> None:
        with pytest.raises(ValueError, match="active fill"):
            RetryMachine(_generating_first()).fill_transient(reason)


class TestRetryFails:
    def test_increments_below_the_cap(self, reason: Reason) -> None:
        state = _retrying(pool=frozenset(), playing=None, attempts=1)
        after = RetryMachine(state).retry_fails(reason)
        assert after.attempts == 2
        assert after.mode is Mode.RETRYING
        assert after.last_error == reason

    def test_requires_retrying(self, reason: Reason) -> None:
        with pytest.raises(ValueError, match="retrying"):
            RetryMachine(_generating_first()).retry_fails(reason)

    def test_requires_below_the_cap(self, reason: Reason) -> None:
        state = _retrying(pool=frozenset(), playing=None, attempts=MAX_RETRY)
        with pytest.raises(ValueError, match="below the cap"):
            RetryMachine(state).retry_fails(reason)


class TestRetryExhausted:
    def test_fails_an_empty_pool_at_the_cap(self, reason: Reason) -> None:
        state = _retrying(pool=frozenset(), playing=None, attempts=MAX_RETRY)
        after = RetryMachine(state).retry_exhausted(reason)
        assert after.mode is Mode.FAILED
        assert after.attempts == 0
        assert after.pool == frozenset()
        assert after.playing is None
        assert after.last_error == reason

    def test_requires_retrying(self, reason: Reason) -> None:
        with pytest.raises(ValueError, match="retrying"):
            RetryMachine(_generating_first()).retry_exhausted(reason)

    def test_requires_the_cap(self, reason: Reason) -> None:
        state = _retrying(pool=frozenset(), playing=None, attempts=1)
        with pytest.raises(ValueError, match="at the cap"):
            RetryMachine(state).retry_exhausted(reason)

    def test_requires_an_empty_pool(
        self, mk: PartFactory, pool_of: PoolFactory, reason: Reason
    ) -> None:
        # The empty-pool guard is what confines hard-failure to the first-track
        # case (finding #4): a non-empty pool never exhausts.
        state = _retrying(pool=pool_of(1, 2), playing=mk(1), attempts=MAX_RETRY)
        with pytest.raises(ValueError, match="empty pool"):
            RetryMachine(state).retry_exhausted(reason)


class TestRetryCapped:
    def test_self_loops_on_a_non_empty_pool(
        self, mk: PartFactory, pool_of: PoolFactory
    ) -> None:
        state = _retrying(pool=pool_of(1, 2), playing=mk(1), attempts=MAX_RETRY)
        after = RetryMachine(state).retry_capped(Reason("still transient"))
        assert after.mode is Mode.RETRYING  # self-loop, not failed
        assert after.attempts == MAX_RETRY  # pinned at the cap
        assert after.playing == mk(1)  # playback untouched
        assert after.pool == pool_of(1, 2)
        assert after.last_error == Reason("still transient")  # advisory refreshed

    def test_requires_retrying(self, reason: Reason) -> None:
        with pytest.raises(ValueError, match="retrying"):
            RetryMachine(_generating_first()).retry_capped(reason)

    def test_requires_the_cap(self, mk: PartFactory, reason: Reason) -> None:
        state = _retrying(pool=frozenset({mk(1)}), playing=mk(1), attempts=1)
        with pytest.raises(ValueError, match="at the cap"):
            RetryMachine(state).retry_capped(reason)

    def test_requires_a_non_empty_pool(self, reason: Reason) -> None:
        state = _retrying(pool=frozenset(), playing=None, attempts=MAX_RETRY)
        with pytest.raises(ValueError, match="non-empty pool"):
            RetryMachine(state).retry_capped(reason)


class TestRecover:
    def test_from_empty_resumes_generation(self) -> None:
        state = _retrying(pool=frozenset(), playing=None, attempts=3)
        after = RetryMachine(state).recover()
        assert after.mode is Mode.GENERATING_FIRST
        assert after.playing is None
        assert after.filling is True
        assert after.attempts == 0
        assert after.last_error is None

    def test_from_pool_resumes_playback(
        self, mk: PartFactory, pool_of: PoolFactory
    ) -> None:
        state = _retrying(pool=pool_of(1, 2), playing=mk(1), attempts=2)
        after = RetryMachine(state).recover()
        assert after.mode is Mode.PLAYING_FILLING
        assert after.playing == mk(1)  # playback intact
        assert after.filling is True
        assert after.attempts == 0
        assert after.last_error is None

    def test_requires_retrying(self, mk: PartFactory) -> None:
        state = _playing_filling(frozenset({mk(1)}), mk(1))
        with pytest.raises(ValueError, match="retrying"):
            RetryMachine(state).recover()
