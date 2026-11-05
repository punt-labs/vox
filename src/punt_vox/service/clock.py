"""Real time for the lifecycle polls, and the bounded poll they all share."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, ClassVar, Self, final

from punt_vox.service.query_answer import QueryAnswer

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
    moment the condition holds; it never sleeps out the rest of the window, and
    its last sleep is cut to what remains, so the wait never runs past its bound.
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

    def now(self) -> float:
        """Return the poll's clock reading, for computing what remains."""
        return self._clock.monotonic()

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
            self._nap(deadline)
        return fetch()

    def answer_within(
        self, ask: Callable[[float], QueryAnswer], deadline_s: float
    ) -> QueryAnswer:
        """Ask until an answer holds, handing each ask the seconds that remain.

        For a check that itself blocks (a ``systemctl`` or ``launchctl``
        query): each call gets the time left before the deadline as its own
        bound, so no check can carry the wait past it. Returns the first
        answer that holds. Otherwise it returns the last answer that came back
        in time -- a real "no" -- and reports a query that never answered only
        when none did: the last query gets only the sliver left after the
        final sleep, and running out of that sliver must not blame a manager
        that had been answering all along. A query that hung with at least one
        interval of budget after a real answer is a real hang: the answer
        stands and names it as ``stalled``. A check is never run with no time
        left, so there is no extra check at the deadline.
        """
        deadline = self._clock.monotonic() + deadline_s
        last = QueryAnswer.of(holds=False)
        heard = False
        while (remaining := deadline - self._clock.monotonic()) > 0:
            answer = ask(remaining)
            if answer.holds:
                return answer
            full = remaining >= self._interval
            last = last.followed_by(answer, heard=heard, full_budget=full)
            heard = heard or not answer.timed_out
            self._nap(deadline)
        return last

    def _nap(self, deadline: float) -> None:
        """Sleep one interval, cut short so the sleep never passes *deadline*."""
        self._clock.sleep(max(0.0, min(self._interval, deadline - self.now())))
