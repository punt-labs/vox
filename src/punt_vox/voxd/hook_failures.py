"""Bounded tracking of unresolved tool failures."""

from __future__ import annotations

from typing import TYPE_CHECKING, Self

if TYPE_CHECKING:
    from punt_vox.voxd.hook_context_projection import HookToolResult, ToolUpdate

_MAX_SUBJECTS = 48


class HookFailures:
    """Track the latest outcome per tool subject, favoring open failures."""

    __slots__ = ("_outcomes",)
    _outcomes: dict[tuple[str, str], tuple[int, ToolUpdate]]

    def __new__(cls) -> Self:
        """Start with no observed tool outcomes."""
        self = super().__new__(cls)
        self._outcomes = {}
        return self

    def observe(self, sequence: int, update: ToolUpdate) -> None:
        """Retain the newest completion even if it arrives out of order."""
        if not update.completed:
            return
        subject = (update.tool, update.subject)
        previous = self._outcomes.get(subject)
        if previous is not None and sequence <= previous[0]:
            return
        self._outcomes[subject] = (sequence, update)
        if len(self._outcomes) > _MAX_SUBJECTS:
            del self._outcomes[min(self._outcomes, key=self._eviction_order)]

    def _eviction_order(self, subject: tuple[str, str]) -> tuple[bool, int]:
        sequence, update = self._outcomes[subject]
        return update.failed, sequence

    def latest(self) -> HookToolResult | None:
        """Return the newest unresolved failure, without exposing its subject."""
        failure = max(
            (item for item in self._outcomes.values() if item[1].failed),
            key=lambda item: item[0],
            default=None,
        )
        if failure is None:
            return None
        sequence, update = failure
        return update.as_result(sequence)
