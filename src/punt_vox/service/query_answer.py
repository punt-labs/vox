"""The answer of one bounded service-manager query.

A types module: a wait that hands each ``systemctl``/``launchctl`` query only
the time it has left needs to tell "the manager said no" from "the manager did
not answer in time", so the message names the right culprit.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
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
    # "": no stall; else a command that hung with a full budget after this
    # answer came back -- the answer stands, and the hang is named beside it
    stalled: str = ""

    @classmethod
    def of(cls, *, holds: bool) -> Self:
        """Return the answer of a query that finished in time."""
        return cls(holds=holds)

    @classmethod
    def no_answer(cls, command: str) -> Self:
        """Return the answer of *command*, which did not finish in time."""
        return cls(holds=False, unanswered=command)

    def followed_by(
        self, later: QueryAnswer, *, heard: bool, full_budget: bool
    ) -> QueryAnswer:
        """Fold the next not-holding answer of a wait into this one.

        A real answer replaces what came before. A timeout replaces it only
        while nothing has answered (*heard* is False). After a real answer, a
        timeout on the last sliver of the wait is dropped -- the manager had
        been answering -- while one with a full poll interval of budget is a
        real hang, named beside the answer that stands.
        """
        if not later.timed_out or not heard:
            return later
        return self.then_stalled(later.unanswered) if full_budget else self

    def then_stalled(self, command: str) -> Self:
        """Return this answer, noting that *command* then stopped answering."""
        return replace(self, stalled=command)

    @property
    def timed_out(self) -> bool:
        """Return True when the query did not answer within its budget."""
        return bool(self.unanswered)
