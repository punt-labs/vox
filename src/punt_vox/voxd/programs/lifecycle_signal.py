"""Lifecycle control signals: turn on, turn off, and retune (the on/off path).

These map one-to-one onto the Program's generation-path lifecycle transitions.
Each is a typed command the :class:`ControlChannel` consumer applies as a single
serialized mutation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, final

if TYPE_CHECKING:
    from punt_vox.voxd.programs.part import Part
    from punt_vox.voxd.programs.program import Program

__all__ = ["TurnOff", "TurnOn", "VibeStyleChange"]


@final
@dataclass(frozen=True, slots=True)
class TurnOn:
    """Enter the pool from ``off`` (Z ``TurnOn``)."""

    def apply(self, program: Program) -> None:
        """Apply the turn-on transition."""
        program.turn_on()


@final
@dataclass(frozen=True, slots=True)
class TurnOff:
    """Cancel the fill and stop playback (Z ``TurnOff``)."""

    def apply(self, program: Program) -> None:
        """Apply the turn-off transition."""
        program.turn_off()


@final
@dataclass(frozen=True, slots=True)
class VibeStyleChange:
    """Retune to a new (vibe, style) key's saved pool (Z ``VibeStyleChange``)."""

    new_pool: frozenset[Part]

    def apply(self, program: Program) -> None:
        """Apply the retune transition against the new pool."""
        program.vibe_style_change(self.new_pool)
