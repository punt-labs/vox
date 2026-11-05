"""Tests for punt_vox.service.signal_escalation -- the shared kill sequence."""

from __future__ import annotations

import signal
from typing import Self, final

import pytest

from punt_vox.service.signal_escalation import SignalEscalation
from punt_vox.service.termination import Termination


@final
class _Target:
    """Scripted send/wait primitives for one process."""

    __slots__ = ("_exits_on", "_gone", "_refuse", "_refuse_kill", "sent", "waits")

    sent: list[signal.Signals]
    waits: list[float]
    _exits_on: signal.Signals | None  # None: no signal makes it exit
    _refuse: bool
    _refuse_kill: bool
    _gone: bool

    def __new__(
        cls,
        *,
        exits_on: signal.Signals | None,
        refuse: bool = False,
        refuse_kill: bool = False,
    ) -> Self:
        self = super().__new__(cls)
        self.sent = []
        self.waits = []
        self._exits_on = exits_on
        self._refuse = refuse
        self._refuse_kill = refuse_kill
        self._gone = False
        return self

    def send(self, sig: signal.Signals) -> bool:
        if self._refuse or (self._refuse_kill and sig == signal.SIGKILL):
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


def test_a_refused_sigkill_after_a_delivered_sigterm_is_kill_refused() -> None:
    """L5: SIGTERM went out; only SIGKILL was refused."""
    target = _Target(exits_on=None, refuse_kill=True)
    assert target.escalation().run(7.0) is Termination.KILL_REFUSED
    assert target.sent == [signal.SIGTERM]
