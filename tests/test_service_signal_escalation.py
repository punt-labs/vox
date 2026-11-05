"""Tests for punt_vox.service.signal_escalation -- the shared kill sequence."""

from __future__ import annotations

import signal

import pytest

from punt_vox.service.signal_escalation import SignalEscalation
from punt_vox.service.termination import Termination


class _Target:
    """Scripted send/wait primitives for one process."""

    def __init__(
        self, *, exits_on: signal.Signals | None, refuse: bool = False
    ) -> None:
        self.sent: list[signal.Signals] = []
        self.waits: list[float] = []
        self._exits_on = exits_on
        self._refuse = refuse
        self._gone = False

    def send(self, sig: signal.Signals) -> bool:
        if self._refuse:
            return False
        self.sent.append(sig)
        if sig == self._exits_on:
            self._gone = True
        return True

    def wait(self, timeout: float) -> bool:
        self.waits.append(timeout)
        return self._gone

    def escalation(self) -> SignalEscalation:
        return SignalEscalation(4242, self.send, self.wait)


def test_exits_on_sigterm() -> None:
    target = _Target(exits_on=signal.SIGTERM)
    assert target.escalation().run(7.0) is Termination.EXITED
    assert target.sent == [signal.SIGTERM]


def test_survives_both_signals() -> None:
    target = _Target(exits_on=None)
    assert target.escalation().run(7.0) is Termination.SURVIVED
    assert target.sent == [signal.SIGTERM, signal.SIGKILL]
    assert sum(target.waits) <= 7.0


@pytest.mark.parametrize("budget", [0.0, 0.5])
def test_below_the_floor_sends_sigterm_only(budget: float) -> None:
    target = _Target(exits_on=None)
    assert target.escalation().run(budget) is Termination.TERM_ONLY
    assert target.sent == [signal.SIGTERM]


def test_a_refused_signal_is_refused() -> None:
    target = _Target(exits_on=None, refuse=True)
    assert target.escalation().run(7.0) is Termination.REFUSED
    assert target.sent == []
