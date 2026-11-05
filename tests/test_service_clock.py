"""Tests for punt_vox.service.clock -- the bounded poll every wait shares."""

from __future__ import annotations

from _lifecycle_fakes import FakeClock

from punt_vox.service.clock import Poll, SystemClock


def test_until_returns_the_moment_the_condition_holds() -> None:
    clock = FakeClock()
    assert Poll(clock).until(lambda: clock.monotonic() >= 1.0, 10.0) is True
    assert 1.0 <= clock.monotonic() < 1.3


def test_until_false_at_the_deadline() -> None:
    clock = FakeClock()
    assert Poll(clock).until(lambda: False, 2.0) is False
    assert clock.monotonic() >= 2.0


def test_until_checks_once_more_at_the_deadline() -> None:
    clock = FakeClock()
    calls = []

    def late() -> bool:
        calls.append(clock.monotonic())
        return clock.monotonic() >= 2.0

    assert Poll(clock, 1.0).until(late, 2.0) is True


def test_first_returns_the_first_value() -> None:
    clock = FakeClock()
    values = iter([None, None, 7])
    assert Poll(clock).first(lambda: next(values), 5.0) == 7
    assert clock.monotonic() > 0


def test_first_none_when_nothing_arrives() -> None:
    assert Poll(FakeClock()).first(lambda: None, 1.0) is None


def test_system_clock_is_monotonic() -> None:
    clock = SystemClock()
    before = clock.monotonic()
    clock.sleep(0)
    assert clock.monotonic() >= before
