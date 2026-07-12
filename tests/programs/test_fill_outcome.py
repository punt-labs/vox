"""Tests for the shared recover-then-dispatch spine of the fill outcomes.

``RecoveringFillOutcome`` is the template both ``Produced`` and
``PermanentFailure`` derive from. A minimal recording subclass exercises the
spine directly: it never interrupts, recovers a retrying Program before
dispatching, routes to the mode-appropriate hook for ``generating_first`` and
``playing_filling``, and drops the outcome in any other mode.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Self, final

from punt_vox.types_programs import Mode, Reason
from punt_vox.voxd.programs import Part, PlaybackPolicy, Program, ProgramState
from punt_vox.voxd.programs.fill_outcome import RecoveringFillOutcome

PartFactory = Callable[[int], Part]


@final
class _Recording(RecoveringFillOutcome):
    """A concrete outcome that records which mode hook the spine fired."""

    __slots__ = ("hooks",)
    hooks: list[str]

    def __new__(cls) -> Self:
        self = super().__new__(cls)
        self.hooks = []
        return self

    def _on_generating_first(self, program: Program) -> None:
        self.hooks.append("generating_first")

    def _on_playing_filling(self, program: Program) -> None:
        self.hooks.append("playing_filling")


def _generating(policy: PlaybackPolicy) -> Program:
    prog = Program(ProgramState.initial(), policy)
    prog.turn_on()
    return prog


def _filling(policy: PlaybackPolicy, mk: PartFactory) -> Program:
    prog = _generating(policy)
    prog.first_track_ok(mk(1))
    prog.fill_ok(mk(2))
    return prog


def test_a_fill_outcome_never_interrupts() -> None:
    assert _Recording().interrupts is False


class TestModeDispatch:
    def test_generating_first_routes_to_its_hook(self, policy: PlaybackPolicy) -> None:
        outcome = _Recording()
        outcome.apply(_generating(policy))
        assert outcome.hooks == ["generating_first"]

    def test_playing_filling_routes_to_its_hook(
        self, policy: PlaybackPolicy, mk: PartFactory
    ) -> None:
        outcome = _Recording()
        outcome.apply(_filling(policy, mk))
        assert outcome.hooks == ["playing_filling"]

    def test_dropped_in_a_non_filling_mode(self, rotating: Program) -> None:
        outcome = _Recording()
        outcome.apply(rotating)  # playing_rotating no longer wants a fill outcome
        assert outcome.hooks == []


class TestRecoverFirst:
    def test_retrying_recovers_then_dispatches(
        self, policy: PlaybackPolicy, mk: PartFactory, reason: Reason
    ) -> None:
        prog = _filling(policy, mk)
        prog.fill_transient(reason)  # retrying, non-empty pool
        outcome = _Recording()
        outcome.apply(prog)
        assert prog.mode is Mode.PLAYING_FILLING  # recovered before dispatch
        assert outcome.hooks == ["playing_filling"]
