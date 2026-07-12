"""The apply-time orphan guard: a stale fill outcome never pollutes a switch.

A generation that settles and posts its outcome can be overtaken in the single
control queue by a ``SwitchProgram``: the writer retargets to another Program,
then drains the now-stale outcome. Without a guard, that outcome applies to the
*switched-in* Program -- a pool gains a Part it never generated (finding #1).
``FreshFillOutcome`` tags each outcome with the Program it ran for and drops it
when the writer has moved on. These tests pin both arms (applies to the origin,
dropped on a mismatch) at the value level and end-to-end through the channel.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, final

from punt_vox.types_programs import Mode, Reason
from punt_vox.voxd.programs import Advance, AdvanceResult, Part, Program, ProgramState
from punt_vox.voxd.programs.control_channel import ControlChannel
from punt_vox.voxd.programs.fill_guard import FreshFillOutcome
from punt_vox.voxd.programs.fill_signal import (
    PermanentFailure,
    Produced,
    TransientFailure,
)

if TYPE_CHECKING:
    import pytest


@final
class _StubPolicy:
    """A never-invoked policy -- these tests exercise generation, not rotation."""

    def next_part(self, pool: tuple[Part, ...], playing: Part | None) -> AdvanceResult:
        return Advance(pool[0])


def _generating() -> Program:
    """Return a fresh Program in ``generating_first`` (turned on, empty pool)."""
    prog = Program(ProgramState.initial(), _StubPolicy())
    prog.turn_on()
    return prog


class TestAppliesToOrigin:
    def test_produced_joins_the_origin_pool(self) -> None:
        origin = _generating()
        part = Part("id001", 1)
        FreshFillOutcome(origin, Produced(part)).apply(origin)
        assert origin.pool == (part,)
        assert origin.mode is Mode.PLAYING_FILLING  # the first Part started it playing

    def test_produced_plays_after_first_track(self) -> None:
        origin = _generating()
        part = Part("id001", 1)
        FreshFillOutcome(origin, Produced(part)).apply(origin)
        assert origin.playing == part


class TestDroppedOnMismatch:
    def test_produced_never_pollutes_a_switched_program(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        origin = _generating()
        switched_in = _generating()  # a different Program instance (a switch)
        with caplog.at_level(logging.INFO):
            FreshFillOutcome(origin, Produced(Part("id001", 1))).apply(switched_in)
        assert switched_in.pool == ()  # the orphan Part did NOT join the new pool
        assert switched_in.mode is origin.mode  # unpolluted, still generating_first
        assert any("stale fill outcome" in r.getMessage() for r in caplog.records)

    def test_permanent_failure_never_pollutes_a_switched_program(self) -> None:
        origin = _generating()
        switched_in = _generating()
        FreshFillOutcome(
            origin, PermanentFailure(Part("id001", 1), Reason("bad_prompt"))
        ).apply(switched_in)
        # A stale permanent failure must not fail the switched-in Program.
        assert switched_in.mode is origin.mode
        assert len(list(switched_in.failed_parts.ordered())) == 0

    def test_transient_failure_never_perturbs_a_switched_program(self) -> None:
        origin = _generating()
        switched_in = _generating()
        FreshFillOutcome(origin, TransientFailure(Reason("429"))).apply(switched_in)
        # A stale transient must not drive the switched-in retry machine.
        assert switched_in.mode is origin.mode
        assert switched_in.state.attempts == 0


class TestInterruptsDelegates:
    def test_wrapper_reports_the_inner_interrupts(self) -> None:
        origin = _generating()
        wrapped = FreshFillOutcome(origin, Produced(Part("id001", 1)))
        assert wrapped.interrupts is False  # a fill outcome never interrupts


class TestThroughTheControlChannel:
    async def test_stale_produced_dropped_after_retarget(self) -> None:
        # Reproduce the race end-to-end: the outcome is tagged while program A is
        # active, the channel is retargeted to B (as SwitchProgram.apply does),
        # then the queued stale outcome is drained -- and must be discarded.
        program_a = _generating()
        program_b = _generating()
        channel = ControlChannel(program_a)
        stale = FreshFillOutcome(program_a, Produced(Part("id001", 1)))
        channel.retarget(program_b)  # the switch landed before the outcome drained
        channel.post(stale)
        await channel.apply_next()
        assert program_b.pool == ()  # B never gained the orphan Part
        assert program_a.pool == ()  # A is abandoned, also untouched

    async def test_fresh_produced_applies_when_not_switched(self) -> None:
        program_a = _generating()
        channel = ControlChannel(program_a)
        part = Part("id001", 1)
        channel.post(FreshFillOutcome(program_a, Produced(part)))
        await channel.apply_next()
        assert program_a.pool == (part,)  # no switch -> the Part joins normally
