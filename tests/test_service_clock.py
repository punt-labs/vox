"""Tests for punt_vox.service.clock -- the bounded poll every wait shares."""

from __future__ import annotations

from _lifecycle_fakes import FakeClock

from punt_vox.service.clock import Poll, SystemClock
from punt_vox.service.query_answer import QueryAnswer


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


def test_until_never_sleeps_past_the_deadline() -> None:
    """The last sleep is cut to what remains, so a 2.5 s wait ends at 2.5 s."""
    clock = FakeClock()
    assert Poll(clock, 1.0).until(lambda: False, 2.5) is False
    assert clock.monotonic() == 2.5


def test_answer_within_hands_each_check_the_time_that_remains() -> None:
    """A check is given at most the budget left, so it cannot overrun the wait."""
    clock = FakeClock()
    seen: list[tuple[float, float]] = []

    def check(within_s: float) -> QueryAnswer:
        seen.append((clock.monotonic(), within_s))
        return QueryAnswer.of(holds=False)

    assert Poll(clock, 1.0).answer_within(check, 2.5).holds is False
    assert seen
    assert all(budget > 0 and at + budget <= 2.5 for at, budget in seen)
    assert clock.monotonic() <= 2.5


def test_answer_within_returns_the_moment_the_check_holds() -> None:
    clock = FakeClock()
    poll = Poll(clock, 0.5)
    answer = poll.answer_within(
        lambda _: QueryAnswer.of(holds=clock.monotonic() >= 1.0), 5.0
    )
    assert answer.holds
    assert clock.monotonic() == 1.0


def test_answer_within_returns_the_last_answer_when_none_holds() -> None:
    """A wait that ran out says why: the last check timed out, or answered no."""
    clock = FakeClock()
    answers = iter([QueryAnswer.of(holds=False)] + [QueryAnswer.no_answer("q")] * 9)
    answer = Poll(clock, 1.0).answer_within(lambda _: next(answers), 2.5)
    assert answer.holds is False
    assert answer.unanswered == "q"
