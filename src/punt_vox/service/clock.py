"""Real time for the lifecycle polls, and the bounded poll they all share."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, ClassVar, Self, final

if TYPE_CHECKING:
    from collections.abc import Callable

    from punt_vox.service.backend import Clock

__all__ = ["Poll", "SystemClock"]


@final
class SystemClock:
    """The process's monotonic clock and a real ``time.sleep``."""

    __slots__ = ()

    def __new__(cls) -> Self:
        return super().__new__(cls)

    def monotonic(self) -> float:
        """Return ``time.monotonic()``."""
        del self
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        """Block for *seconds* of real time."""
        del self
        time.sleep(seconds)


@final
class Poll:
    """Re-check a condition every interval until it holds or a deadline passes.

    The check runs once more at the deadline, so a condition that becomes true
    during the last sleep is not reported as a timeout. The poll returns the
    moment the condition holds; it never sleeps out the rest of the window.
    """

    __slots__ = ("_clock", "_interval")

    INTERVAL_S: ClassVar[float] = 0.2

    _clock: Clock
    _interval: float

    def __new__(cls, clock: Clock, interval: float = INTERVAL_S) -> Self:
        self = super().__new__(cls)
        self._clock = clock
        self._interval = interval
        return self

    def until(self, condition: Callable[[], bool], deadline_s: float) -> bool:
        """Return True once *condition* holds, False if it never does in time."""
        return self.first(lambda: True if condition() else None, deadline_s) is True

    def first[T](self, fetch: Callable[[], T | None], deadline_s: float) -> T | None:
        """Return the first value *fetch* produces, polling until the deadline.

        ``None`` from *fetch* means "not yet"; ``None`` returned means nothing
        was produced before the deadline.
        """
        deadline = self._clock.monotonic() + deadline_s
        while self._clock.monotonic() < deadline:
            value = fetch()
            if value is not None:
                return value
            self._clock.sleep(self._interval)
        return fetch()
