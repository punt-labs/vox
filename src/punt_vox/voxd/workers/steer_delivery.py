"""Authorize dialog choices separately from ordinary literal worker input."""

from __future__ import annotations

import unicodedata
from typing import TYPE_CHECKING, Self, final

if TYPE_CHECKING:
    from punt_vox.voxd.workers.contracts import HarnessProfile, WorkerProvider
    from punt_vox.voxd.workers.model import WorkerRecord
    from punt_vox.voxd.workers.steering_types import DecisionChoice


@final
class SteerDelivery:
    """Bind the harness's pending input state to its provider's safe delivery path."""

    __slots__ = ("_profile", "_provider")
    _profile: HarnessProfile
    _provider: WorkerProvider

    def __new__(cls, profile: HarnessProfile, provider: WorkerProvider) -> Self:
        """Bind the resolved harness and transport for one admitted delivery."""
        self = super().__new__(cls)
        self._profile, self._provider = profile, provider
        return self

    def send(
        self,
        record: WorkerRecord,
        words: str,
        *,
        urgent: bool,
        choice: DecisionChoice | None,  # None submits ordinary follow-up text.
    ) -> int:
        """Refuse over-long or ambiguous dialog input before any provider call."""
        self._fits(words)
        previous = self._profile.last_input(record.id)
        pending = previous is not None and previous.decision
        if pending != (choice is not None):
            raise ValueError(
                "Pending decision: ask yes/no and pass choice=approve or "
                "choice=deny; never send free text"
                if pending
                else "choice requires a pending worker decision"
            )
        if pending and urgent:
            raise ValueError(
                "A decision choice must not interrupt the permission dialog"
            )
        if choice is not None:
            self._provider.decide(record.session, choice, profile=self._profile)
        else:
            self._provider.steer(
                record.session, words, urgent=urgent, profile=self._profile
            )
        return previous.seq if previous is not None else 0

    @staticmethod
    def validate(words: str) -> None:
        """Refuse empty or control-laden words before any worker is looked up."""
        if not words.strip():
            raise ValueError("Worker words must not be empty or whitespace")
        if any(unicodedata.category(c) in ("Cc", "Zl", "Zp") for c in words):
            raise ValueError(
                "Worker words must not contain control characters or newlines"
            )

    def _fits(self, words: str) -> None:
        """Refuse words the harness's input receipt cannot hold."""
        if len(words) > self._profile.input_limit:
            raise ValueError(
                f"Words exceed receipt limit of {self._profile.input_limit} characters"
            )
