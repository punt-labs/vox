"""One time bound an operation shares across every blocking step it takes.

``vox daemon status`` inspects the port (``fuser``/``lsof``), each holder
(``ps``), maybe the listener (a TCP connect), then sends one health request.
Each step has its own timeout; summed, they could run far past the bound the
operator was promised. A :class:`Deadline` is started once and handed to every
step, which asks it to :meth:`~Deadline.cap` its own timeout to what is left.
A step started after the bound has passed raises instead of running.
"""

from __future__ import annotations

import math
import time
from typing import TYPE_CHECKING, Self, final

from punt_vox.service.backend import PortProbeError

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["Deadline"]


@final
class Deadline:
    """A point on a monotonic clock that every step of one operation honours."""

    __slots__ = ("_at", "_now", "_within_s")

    _now: Callable[[], float]
    _within_s: float
    _at: float

    def __new__(cls, now: Callable[[], float], within_s: float) -> Self:
        """Start a deadline *within_s* seconds after *now()* reads."""
        self = super().__new__(cls)
        self._now = now
        self._within_s = within_s
        self._at = now() + within_s
        return self

    @classmethod
    def never(cls) -> Self:
        """Return the deadline that never passes: every step keeps its own timeout."""
        return cls(time.monotonic, math.inf)

    def remaining(self) -> float:
        """Return the seconds left; 0.0 once the deadline has passed."""
        return max(0.0, self._at - self._now())

    def cap(self, seconds: float, what: str) -> float:
        """Return *seconds* cut to what is left of the deadline.

        Raises ``PortProbeError`` naming *what* when nothing is left, so a step
        that cannot start in time fails closed instead of running unbounded.
        """
        left = self.remaining()
        if left <= 0.0:
            msg = f"{what}: the {self._within_s:g}s deadline passed"
            raise PortProbeError(msg)
        return min(seconds, left)
