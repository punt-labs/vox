"""Reconcile the background fill to the Program's ``filling`` flag (one concern).

After every applied command the single writer must bring the :class:`Filler` in
line with the Program: start it on the active plan when generation is wanted,
cancel it otherwise. That decision is its own responsibility, so it lives here
rather than in :class:`ControlChannel` -- the channel owns serialization, the
reconciler owns the fill lifecycle. The channel holds one reconciler and calls
:meth:`reconcile` after each apply; nothing else touches the fill.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, Self, final

from punt_vox.voxd.programs.mode import Mode

if TYPE_CHECKING:
    from punt_vox.voxd.programs.filler import FillPlan, FillPlanSource
    from punt_vox.voxd.programs.program import Program

__all__ = ["Fill", "FillReconciler"]


class Fill(Protocol):
    """The fill lifecycle the reconciler drives: run to plan, or stop."""

    def ensure_running(self, plan: FillPlan) -> None:
        """Start (or keep) the background fill working toward ``plan``."""
        ...

    def cancel(self) -> None:
        """Stop any in-flight generation."""
        ...


@final
class FillReconciler:
    """Match the background fill to a Program's ``filling`` flag after each apply."""

    __slots__ = ("_filler", "_plan_source")
    _filler: Fill
    _plan_source: FillPlanSource

    def __new__(cls, filler: Fill, plan_source: FillPlanSource) -> Self:
        self = super().__new__(cls)
        self._filler = filler
        self._plan_source = plan_source
        return self

    def reconcile(self, program: Program) -> None:
        """Keep the fill running while the Program wants generation, else cancel it.

        "Wants generation" is ``filling`` OR ``retrying``. During a transient
        backoff the model's ``filling`` flag is false (``FillOk`` is disabled),
        but generation is paused, not stopped: the fill task is the retry engine
        that keeps trying at the capped backoff and resumes via ``recover`` when
        a Part finally lands (finding #4). Cancelling it there would strand the
        Program in ``retrying`` forever after a single transient error.
        """
        if program.state.filling or program.mode is Mode.RETRYING:
            self._filler.ensure_running(self._plan_source.current_plan())
        else:
            self._filler.cancel()
