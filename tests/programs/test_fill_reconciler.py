"""Tests for FillReconciler -- start the fill when filling, cancel it otherwise."""

from __future__ import annotations

from typing import TYPE_CHECKING, Self, final

from punt_vox.voxd.programs.fill_reconciler import FillReconciler
from punt_vox.voxd.programs.program import Program
from punt_vox.voxd.programs.rotate_policy import RotatePolicy
from punt_vox.voxd.programs.state import ProgramState

if TYPE_CHECKING:
    from punt_vox.voxd.programs.filler import FillPlan


@final
class _RecordingFiller:
    """Capture the reconciler's fill decisions without running any generation."""

    __slots__ = ("cancels", "started")
    started: list[FillPlan]
    cancels: int

    def __new__(cls) -> Self:
        self = super().__new__(cls)
        self.started = []
        self.cancels = 0
        return self

    def ensure_running(self, plan: FillPlan) -> None:
        self.started.append(plan)

    def cancel(self) -> None:
        self.cancels += 1


@final
class _StubPlanSource:
    """Return a sentinel plan so the reconciler's start path is observable."""

    __slots__ = ("plan",)
    plan: object

    def __new__(cls) -> Self:
        self = super().__new__(cls)
        self.plan = object()
        return self

    def current_plan(self) -> FillPlan:
        return self.plan  # type: ignore[return-value]  # sentinel stands in for FillPlan


def _filling_program() -> Program:
    """Return a Program driven to generating_first (filling True)."""
    program = Program(ProgramState.initial(), RotatePolicy())
    program.turn_on()
    return program


def _off_program() -> Program:
    """Return a fresh Program in the off state (filling False)."""
    return Program(ProgramState.initial(), RotatePolicy())


class TestReconcile:
    """reconcile mirrors the Program's filling flag onto the fill."""

    def test_starts_the_fill_on_the_active_plan_when_filling(self) -> None:
        filler = _RecordingFiller()
        source = _StubPlanSource()
        FillReconciler(filler, source).reconcile(_filling_program())
        assert filler.started == [source.plan]
        assert filler.cancels == 0

    def test_cancels_the_fill_when_not_filling(self) -> None:
        filler = _RecordingFiller()
        FillReconciler(filler, _StubPlanSource()).reconcile(_off_program())
        assert filler.started == []
        assert filler.cancels == 1
