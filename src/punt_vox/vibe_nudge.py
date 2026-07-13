"""Cadence logic for the agent-driven auto-vibe reminder.

The auto vibe is set by the main agent, not derived from tool output. A
``UserPromptSubmit`` hook nudges the agent every ``N``th prompt to glance at
the session and set the vibe if the mood shifted. This module owns the pure
decision — increment, fire, reset — with no I/O; the hook layer reads and
writes the counter and emits the reminder.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Self

__all__ = ["DEFAULT_THRESHOLD", "VIBE_NUDGE_REMINDER", "NudgeDecision", "VibeNudge"]

DEFAULT_THRESHOLD = 5

# Silent context injected as UserPromptSubmit additionalContext (never spoken):
# the main agent, which sees the whole conversation, reads this and sets the
# session vibe. A fixed string, not a random pool — the user never hears it and
# a deterministic value is testable. It lives beside the cadence that emits it,
# not in the speech quip registry, because it is context, not speech.
VIBE_NUDGE_REMINDER = (
    "Vibe check: glance at how this session is going. If the mood has clearly "
    "shifted — smooth sailing, deep focus, grinding through failures, relief "
    "after a hard-won fix — call the vibe tool with a matching mood and tags. "
    "If nothing has changed, ignore this and carry on."
)


@dataclass(frozen=True, slots=True)
class NudgeDecision:
    """The outcome of advancing the cadence for one prompt.

    ``reminder`` is the context to inject, or ``None`` to stay silent — absence
    is the documented contract for "no nudge this prompt" (PY-TS-14), not a
    failure to produce a value.
    """

    next_turns: int
    reminder: str | None


class VibeNudge:
    """A mod-``N`` cadence: fire the reminder every ``threshold`` auto prompts."""

    __slots__ = ("_reminder", "_threshold")

    _threshold: int
    _reminder: str

    def __new__(
        cls, threshold: int = DEFAULT_THRESHOLD, reminder: str = VIBE_NUDGE_REMINDER
    ) -> Self:
        if threshold < 1:
            msg = f"threshold must be at least 1, got {threshold}"
            raise ValueError(msg)
        self = super().__new__(cls)
        self._threshold = threshold
        self._reminder = reminder
        return self

    def advance(self, *, mode: str, turns: int) -> NudgeDecision:
        """Return the counter after this prompt and the reminder to inject, if any.

        Outside ``auto`` the counter is untouched and nothing fires — the user
        owns the vibe in ``manual`` and there is none in ``off``. In ``auto`` the
        counter increments; at the threshold it fires and resets to zero.
        """
        if mode != "auto":
            return NudgeDecision(next_turns=turns, reminder=None)
        advanced = turns + 1
        if advanced >= self._threshold:
            return NudgeDecision(next_turns=0, reminder=self._reminder)
        return NudgeDecision(next_turns=advanced, reminder=None)
