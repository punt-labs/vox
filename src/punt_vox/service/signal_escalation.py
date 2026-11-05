"""SIGTERM, a grace, then SIGKILL: the one sequence every voxd handle runs.

Both process handles (pidfd on Linux, start-time re-check elsewhere) terminate
the same way and differ only in how they signal and how they wait for an exit.
:class:`SignalEscalation` owns the sequence and its budget split, and reports
the outcome as a :class:`~punt_vox.service.termination.Termination`, so the
caller learns which signals went out and whether the kernel refused them.
"""

from __future__ import annotations

import logging
import signal
from typing import TYPE_CHECKING, ClassVar, Self, final

from punt_vox.service.backend import KILL_GRACE_FLOOR_S
from punt_vox.service.termination import Termination

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

__all__ = ["SignalEscalation"]

type Sender = Callable[[signal.Signals], bool]
"""Deliver a signal; False when the kernel refused it (permission denied)."""

type Waiter = Callable[[float], bool]
"""Wait up to the given seconds; True once the process has exited."""


@final
class SignalEscalation:
    """Terminate one process within a budget through its handle's primitives."""

    __slots__ = ("_pid", "_send", "_wait")

    # SIGTERM grace, then the wait for SIGKILL to take effect, when the
    # caller's budget allows both in full.
    TERM_GRACE_S: ClassVar[float] = 5.0
    KILL_WAIT_S: ClassVar[float] = 2.0

    _pid: int
    _send: Sender
    _wait: Waiter

    def __new__(cls, pid: int, send: Sender, wait: Waiter) -> Self:
        # *send* and *wait* are the handle's own: they reach only the pinned
        # process, never a bare, reusable pid.
        self = super().__new__(cls)
        self._pid = pid
        self._send = send
        self._wait = wait
        return self

    def run(self, within_s: float) -> Termination:
        """SIGTERM, then SIGKILL after the grace; take at most *within_s*.

        Below ``KILL_GRACE_FLOOR_S`` only SIGTERM is sent: a SIGKILL with no
        grace before it is never sent.
        """
        grace, kill_wait = self._split(within_s)
        if not self._send(signal.SIGTERM):
            return self._unless_gone(Termination.REFUSED)
        if self._wait(grace):
            return Termination.EXITED
        if kill_wait is None:
            return self._unless_gone(Termination.TERM_ONLY)
        logger.warning("voxd pid %d ignored SIGTERM; sending SIGKILL", self._pid)
        if not self._send(signal.SIGKILL):
            return self._unless_gone(Termination.REFUSED)
        return Termination.EXITED if self._wait(kill_wait) else Termination.SURVIVED

    def _unless_gone(self, outcome: Termination) -> Termination:
        """Return EXITED if the process has gone anyway, else *outcome*."""
        return Termination.EXITED if self._wait(0.0) else outcome

    @classmethod
    def _split(cls, within_s: float) -> tuple[float, float | None]:
        """Split a terminate budget into (SIGTERM grace, SIGKILL wait).

        The SIGKILL wait is ``None`` -- send no SIGKILL -- when the budget is
        below ``KILL_GRACE_FLOOR_S``.
        """
        if within_s < KILL_GRACE_FLOOR_S:
            return max(0.0, within_s), None
        kill_wait = min(cls.KILL_WAIT_S, within_s * 0.3)
        return min(cls.TERM_GRACE_S, within_s - kill_wait), kill_wait
