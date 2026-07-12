"""Mood derivation from a bounded window of command outcomes.

The mood is a total function of two numbers taken from the window: the length
of the trailing run of consecutive failures, and whether a failure sits in
recent memory. ``docs/vibe-exit-code.tex`` is the formal model this implements;
the five constants and their invariants are named there.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final


class Mood(Enum):
    """One of five voices auto-vibe selects, deepening with the failure run."""

    HAPPY = "happy"
    FOCUSED = "focused"
    FRUSTRATED = "frustrated"
    WEARY = "weary"
    RELIEVED = "relieved"

    @property
    def tags(self) -> str:
        """Return the ElevenLabs expressive tags for this mood."""
        return _MOOD_TAGS[self]


_MOOD_TAGS: Final[dict[Mood, str]] = {
    Mood.HAPPY: "[happy]",
    Mood.FOCUSED: "[focused]",
    Mood.FRUSTRATED: "[frustrated] [sighs]",
    Mood.WEARY: "[weary]",
    Mood.RELIEVED: "[relieved]",
}


@dataclass(frozen=True, slots=True)
class MoodThresholds:
    """The five constants that shape the mood machine, and the model's invariants.

    Two invariants are load-bearing: ``focus_from`` must be exactly 1, or the
    ``run == 0`` cases stop partitioning cleanly and the derivation is no longer
    total; and ``weary_from`` must sit below ``max_window``, or FIFO eviction can
    mask ``weary``. Construction enforces both — their silent violation would
    break the formal model.
    """

    focus_from: int
    frust_from: int
    weary_from: int
    recent_k: int
    max_window: int

    def __post_init__(self) -> None:
        """Reject constants that break the mood derivation."""
        if self.focus_from != 1:
            msg = f"focus_from must be 1 for a total mood, got {self.focus_from}"
            raise ValueError(msg)
        if self.weary_from >= self.max_window:
            msg = (
                f"weary_from ({self.weary_from}) must be below "
                f"max_window ({self.max_window}) or eviction masks weary"
            )
            raise ValueError(msg)
        if not self.focus_from < self.frust_from < self.weary_from:
            msg = (
                "run bands require focus_from < frust_from < weary_from, got "
                f"{self.focus_from}, {self.frust_from}, {self.weary_from}"
            )
            raise ValueError(msg)
        if self.recent_k < 1:
            msg = f"recent_k must be at least 1, got {self.recent_k}"
            raise ValueError(msg)

    def mood_for(self, run: int, *, recent_fail: bool) -> Mood:
        """Return the mood for a trailing failure *run* and recency flag.

        The bands are half-open: ``[focus_from, frust_from)`` is focused,
        ``[frust_from, weary_from)`` is frustrated, ``[weary_from, inf)`` is
        weary. A ``run`` of 0 splits on *recent_fail* into relieved or happy —
        an empty or clean window is happy, never a default frustrated.
        """
        if run >= self.weary_from:
            return Mood.WEARY
        if run >= self.frust_from:
            return Mood.FRUSTRATED
        if run >= self.focus_from:
            return Mood.FOCUSED
        return Mood.RELIEVED if recent_fail else Mood.HAPPY


DEFAULT_THRESHOLDS: Final = MoodThresholds(
    focus_from=1, frust_from=3, weary_from=5, recent_k=3, max_window=20
)

__all__ = ["DEFAULT_THRESHOLDS", "Mood", "MoodThresholds"]
