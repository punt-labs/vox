"""Fill-outcome control signals -- what the :class:`Filler` posts per Part.

The Filler is mode-agnostic: it produces a Part (or fails) and posts one of
three outcome signals. Each signal's ``apply`` maps the outcome to the right
Program transition *by the Program's current mode*, so all the mode->transition
dispatch (including the resilience states) lives in one typed place rather than
in the loop. A transient outcome drives the modeled retry machine
(``retry_fails`` below the cap, ``retry_exhausted`` at the cap on an empty pool);
an outcome that lands while the Program is ``retrying`` first ``recover``s.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, final

from punt_vox.voxd.programs.fill_outcome import RecoveringFillOutcome
from punt_vox.voxd.programs.format import MAX_RETRY
from punt_vox.voxd.programs.mode import Mode

if TYPE_CHECKING:
    from punt_vox.voxd.programs.identifiers import Reason
    from punt_vox.voxd.programs.part import Part
    from punt_vox.voxd.programs.program import Program

__all__ = ["PermanentFailure", "Produced", "TransientFailure"]


@final
@dataclass(frozen=True, slots=True)
class Produced(RecoveringFillOutcome):
    """A Part was generated and is ready to join the pool."""

    part: Part

    def _on_generating_first(self, program: Program) -> None:
        program.first_track_ok(self.part)

    def _on_playing_filling(self, program: Program) -> None:
        program.fill_ok(self.part)


@final
@dataclass(frozen=True, slots=True)
class PermanentFailure(RecoveringFillOutcome):
    """A Part hit a permanent generation error (bad prompt / ToS / missing key)."""

    part: Part
    reason: Reason

    def _on_generating_first(self, program: Program) -> None:
        program.first_track_bad_prompt(self.part, self.reason)

    def _on_playing_filling(self, program: Program) -> None:
        program.fill_bad_part(self.part, self.reason)


@final
@dataclass(frozen=True, slots=True)
class TransientFailure:
    """A Part hit a transient generation error (429 / quota / 5xx / timeout)."""

    reason: Reason

    @property
    def interrupts(self) -> bool:
        """A transient backoff pauses generation, never the existing playback."""
        return False

    def apply(self, program: Program) -> None:
        """Drive the modeled retry machine by the Program's current mode."""
        mode = program.mode
        if mode is Mode.GENERATING_FIRST:
            program.first_track_transient(self.reason)
        elif mode is Mode.PLAYING_FILLING:
            program.fill_transient(self.reason)
        elif mode is Mode.RETRYING:
            self._continue_retry(program)
        # else: drop -- no fill is in flight to fail.

    def _continue_retry(self, program: Program) -> None:
        exhausted = program.state.attempts >= MAX_RETRY and len(program.pool) == 0
        if exhausted:
            program.retry_exhausted(self.reason)
        else:
            program.retry_fails(self.reason)
