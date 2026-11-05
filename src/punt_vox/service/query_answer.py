"""The answer of one bounded service-manager query.

A types module: a wait that hands each ``systemctl``/``launchctl`` query only
the time it has left needs to tell "the manager said no" from "the manager did
not answer in time", so the message names the right culprit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Self, final

__all__ = ["QueryAnswer"]


@final
@dataclass(frozen=True, slots=True)
class QueryAnswer:
    """What one bounded service-manager query answered.

    ``holds`` is the answer when the query finished in time. A query that ran
    out of its budget confirms nothing: ``holds`` is False and ``unanswered``
    names the command, so a wait that gives up can say the manager hung
    rather than blame the job.
    """

    holds: bool
    unanswered: str = ""  # "": the query answered; else the command that hung

    @classmethod
    def of(cls, *, holds: bool) -> Self:
        """Return the answer of a query that finished in time."""
        return cls(holds=holds)

    @classmethod
    def no_answer(cls, command: str) -> Self:
        """Return the answer of *command*, which did not finish in time."""
        return cls(holds=False, unanswered=command)

    @property
    def timed_out(self) -> bool:
        """Return True when the query did not answer within its budget."""
        return bool(self.unanswered)
