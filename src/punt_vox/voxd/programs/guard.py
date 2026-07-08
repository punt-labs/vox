"""The guard-violation exception every Z transition raises on a failed precondition."""

from __future__ import annotations

from typing import NoReturn, final

__all__ = ["GuardViolationError"]


@final
class GuardViolationError(ValueError):
    """A command's Z precondition no longer holds -- a benign lost race.

    Raised through :meth:`reject` when a guard fails (e.g. a ``rotate`` that
    arrives just after a ``turn_off``). It subclasses ``ValueError`` so callers
    that only distinguish "illegal transition" keep working, but the single
    writer catches *this* type alone: a plain ``ValueError`` from
    :class:`StateInvariants` means a corrupt successor -- a bug, not a race --
    and must surface at ERROR rather than be mislabeled as a losing racer.
    """

    @staticmethod
    def reject(message: str) -> NoReturn:
        """Raise a guard violation -- a violated Z precondition (guard)."""
        raise GuardViolationError(message)
