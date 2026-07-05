"""Tests for the fill-outcome signals' mode-dispatch and resilience mapping."""

from __future__ import annotations

from collections.abc import Callable

from punt_vox.voxd.programs import (
    MAX_RETRY,
    Mode,
    Part,
    PlaybackPolicy,
    Program,
    ProgramState,
    Reason,
)
from punt_vox.voxd.programs.fill_signal import (
    PermanentFailure,
    Produced,
    TransientFailure,
)

PartFactory = Callable[[int], Part]


def _generating(policy: PlaybackPolicy) -> Program:
    prog = Program(ProgramState.initial(), policy)
    prog.turn_on()
    return prog


def _filling(policy: PlaybackPolicy, mk: PartFactory) -> Program:
    prog = _generating(policy)
    prog.first_track_ok(mk(1))
    prog.fill_ok(mk(2))
    return prog


class TestProduced:
    def test_generating_first_starts_playing(
        self, policy: PlaybackPolicy, mk: PartFactory
    ) -> None:
        prog = _generating(policy)
        Produced(mk(1)).apply(prog)
        assert prog.mode is Mode.PLAYING_FILLING
        assert prog.playing == mk(1)

    def test_playing_filling_grows_pool(
        self, policy: PlaybackPolicy, mk: PartFactory
    ) -> None:
        prog = _filling(policy, mk)
        Produced(mk(3)).apply(prog)
        assert len(prog.pool) == 3

    def test_retrying_empty_recovers_then_starts(
        self, policy: PlaybackPolicy, mk: PartFactory, reason: Reason
    ) -> None:
        prog = _generating(policy)
        prog.first_track_transient(reason)  # retrying, empty pool
        Produced(mk(1)).apply(prog)
        assert prog.mode is Mode.PLAYING_FILLING
        assert prog.playing == mk(1)

    def test_retrying_nonempty_recovers_then_fills(
        self, policy: PlaybackPolicy, mk: PartFactory, reason: Reason
    ) -> None:
        prog = _filling(policy, mk)
        prog.fill_transient(reason)  # retrying, non-empty pool
        Produced(mk(3)).apply(prog)
        assert prog.mode is Mode.PLAYING_FILLING
        assert len(prog.pool) == 3

    def test_dropped_when_not_filling(self, rotating: Program, mk: PartFactory) -> None:
        before = rotating.mode
        Produced(mk(99)).apply(rotating)  # rotating no longer wants a fill
        assert rotating.mode is before
        assert mk(99) not in rotating.pool


class TestPermanentFailure:
    def test_generating_first_fails_program(
        self, policy: PlaybackPolicy, mk: PartFactory, reason: Reason
    ) -> None:
        prog = _generating(policy)
        PermanentFailure(mk(1), reason).apply(prog)
        assert prog.mode is Mode.FAILED

    def test_playing_filling_records_per_part(
        self, policy: PlaybackPolicy, mk: PartFactory, reason: Reason
    ) -> None:
        prog = _filling(policy, mk)
        PermanentFailure(mk(9), reason).apply(prog)
        assert prog.mode is Mode.PLAYING_FILLING  # stays healthy
        assert mk(9) in prog.failed_parts

    def test_retrying_recovers_then_records(
        self, policy: PlaybackPolicy, mk: PartFactory, reason: Reason
    ) -> None:
        prog = _filling(policy, mk)
        prog.fill_transient(reason)  # retrying, non-empty pool
        PermanentFailure(mk(9), reason).apply(prog)
        assert prog.mode is Mode.PLAYING_FILLING  # recovered, then per-part fail
        assert mk(9) in prog.failed_parts

    def test_dropped_when_rotating(self, rotating: Program, mk: PartFactory) -> None:
        PermanentFailure(mk(99), reason=Reason("x")).apply(rotating)
        assert rotating.mode is Mode.PLAYING_ROTATING  # unchanged
        assert mk(99) not in rotating.failed_parts


class TestTransientFailure:
    def test_generating_first_backs_off(
        self, policy: PlaybackPolicy, reason: Reason
    ) -> None:
        prog = _generating(policy)
        TransientFailure(reason).apply(prog)
        assert prog.mode is Mode.RETRYING
        assert prog.state.attempts == 1

    def test_playing_filling_backs_off(
        self, policy: PlaybackPolicy, mk: PartFactory, reason: Reason
    ) -> None:
        prog = _filling(policy, mk)
        TransientFailure(reason).apply(prog)
        assert prog.mode is Mode.RETRYING

    def test_retrying_below_cap_counts(
        self, policy: PlaybackPolicy, reason: Reason
    ) -> None:
        prog = _generating(policy)
        prog.first_track_transient(reason)  # retrying, attempts 1
        TransientFailure(reason).apply(prog)
        assert prog.state.attempts == 2

    def test_retrying_at_cap_empty_exhausts(
        self, policy: PlaybackPolicy, reason: Reason
    ) -> None:
        prog = _generating(policy)
        prog.first_track_transient(reason)  # attempts 1
        for _ in range(MAX_RETRY - 1):  # drive attempts to the cap
            TransientFailure(reason).apply(prog)
        assert prog.state.attempts == MAX_RETRY
        TransientFailure(reason).apply(prog)  # at the cap, empty pool -> give up
        assert prog.mode is Mode.FAILED


def test_fill_signals_never_interrupt(mk: PartFactory) -> None:
    # Fill outcomes join the pool / record a failure; they never cut off playback.
    assert Produced(mk(1)).interrupts is False
    assert PermanentFailure(mk(1), Reason("x")).interrupts is False
    assert TransientFailure(Reason("x")).interrupts is False
