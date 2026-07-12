"""A bounded FIFO window of command outcomes, and the mood it implies.

Each Bash command contributes one :class:`Outcome` — ``ok`` for exit 0, ``fail``
for any non-zero exit. The window keeps the most recent outcomes and derives the
session mood from its trailing failure run. ``docs/vibe-exit-code.tex`` is the
formal model; the window serializes into the reused ``vibe_signals`` config field.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Self

from punt_vox.vibe_mood import DEFAULT_THRESHOLDS, Mood, MoodThresholds

logger = logging.getLogger(__name__)


class Outcome(Enum):
    """A single command's result, classified by its exit code."""

    OK = "ok"
    FAIL = "fail"

    @property
    def token(self) -> str:
        """Return the wire token stored in ``vibe_signals``."""
        return self.value

    @classmethod
    def from_exit_code(cls, exit_code: int) -> Outcome:
        """Return ``OK`` for a zero exit, ``FAIL`` for any non-zero exit."""
        return cls.OK if exit_code == 0 else cls.FAIL

    @classmethod
    def from_token(cls, token: str) -> Outcome:
        """Parse a wire token; raise ``ValueError`` on anything but ok/fail."""
        return cls(token)


class OutcomeWindow:
    """Bounded FIFO of outcomes that derives the session mood on demand."""

    __slots__ = ("_outcomes", "_thresholds")

    _outcomes: list[Outcome]
    _thresholds: MoodThresholds

    def __new__(cls, thresholds: MoodThresholds = DEFAULT_THRESHOLDS) -> Self:
        self = super().__new__(cls)
        self._outcomes = []
        self._thresholds = thresholds
        return self

    def record(self, outcome: Outcome) -> None:
        """Append *outcome*, evicting the oldest once past ``max_window``."""
        self._outcomes.append(outcome)
        cap = self._thresholds.max_window
        if len(self._outcomes) > cap:
            del self._outcomes[:-cap]

    @property
    def run_fail(self) -> int:
        """Length of the trailing run of consecutive FAIL (0 if last is OK)."""
        run = 0
        for outcome in reversed(self._outcomes):
            if outcome is not Outcome.FAIL:
                break
            run += 1
        return run

    @property
    def recent_fail(self) -> bool:
        """Whether a FAIL sits among the last ``recent_k`` outcomes."""
        recent = self._outcomes[-self._thresholds.recent_k :]
        return Outcome.FAIL in recent

    @property
    def mood(self) -> Mood:
        """Derive the current mood from the trailing run and recency."""
        return self._thresholds.mood_for(self.run_fail, recent_fail=self.recent_fail)

    def resolve_tags(self) -> str:
        """Return the ElevenLabs tags for the current mood."""
        return self.mood.tags

    def serialize(self) -> str:
        """Serialize the window to the comma-separated ``vibe_signals`` form."""
        return ",".join(o.token for o in self._outcomes)

    @classmethod
    def deserialize(
        cls, raw: str, thresholds: MoodThresholds = DEFAULT_THRESHOLDS
    ) -> Self:
        """Parse a comma-separated token string, skipping malformed tokens."""
        window = cls(thresholds)
        for token in (t.strip() for t in raw.split(",") if t.strip()):
            try:
                window.record(Outcome.from_token(token))
            except ValueError:
                logger.warning(
                    "OutcomeWindow.deserialize: skipping malformed token %r", token
                )
        return window

    def __len__(self) -> int:
        """Return the number of outcomes in the window."""
        return len(self._outcomes)


__all__ = ["Outcome", "OutcomeWindow"]
