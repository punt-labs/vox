"""Tests for the outcome window (src/punt_vox/vibe_window.py).

Tests are named after the schemas and property schemas in the Z model
docs/vibe-exit-code.tex so the code and the formal model stay tied together.
"""

from __future__ import annotations

import pytest

from punt_vox.vibe_mood import DEFAULT_THRESHOLDS, Mood
from punt_vox.vibe_window import Outcome, OutcomeWindow

_MAX = DEFAULT_THRESHOLDS.max_window

# The arc's trouble-depth ranking: a fail must never lower it, an ok never raise it.
_MOOD_RANK = {
    Mood.HAPPY: 0,
    Mood.RELIEVED: 1,
    Mood.FOCUSED: 2,
    Mood.FRUSTRATED: 3,
    Mood.WEARY: 4,
}


def _window(*tokens: str) -> OutcomeWindow:
    """Build a window from a sequence of ok/fail tokens, oldest first."""
    return OutcomeWindow.deserialize(",".join(tokens))


class TestOutcome:
    """Exit code classifies an outcome; ok/fail is the whole vocabulary."""

    def test_zero_exit_is_ok(self) -> None:
        assert Outcome.from_exit_code(0) is Outcome.OK

    def test_nonzero_exit_is_fail(self) -> None:
        assert Outcome.from_exit_code(1) is Outcome.FAIL

    def test_negative_exit_is_fail(self) -> None:
        assert Outcome.from_exit_code(-1) is Outcome.FAIL

    def test_token_roundtrip(self) -> None:
        assert Outcome.from_token(Outcome.OK.token) is Outcome.OK
        assert Outcome.from_token(Outcome.FAIL.token) is Outcome.FAIL

    def test_unknown_token_raises(self) -> None:
        with pytest.raises(ValueError, match="bogus"):
            Outcome.from_token("bogus")


class TestSerialization:
    """The window serializes to and from the reused vibe_signals field."""

    def test_empty_serializes_to_blank(self) -> None:
        assert OutcomeWindow().serialize() == ""

    def test_roundtrip(self) -> None:
        assert _window("ok", "fail", "ok").serialize() == "ok,fail,ok"

    def test_deserialize_skips_malformed_tokens(self) -> None:
        # Any token that is not ok/fail is dropped, not kept.
        window = _window("ok", "bogus", "fail")
        assert window.serialize() == "ok,fail"

    def test_deserialize_all_malformed_is_empty(self) -> None:
        assert len(_window("bogus", "junk")) == 0


class TestDefaultHappy:
    """An empty or clean window is happy — the derivation has no default."""

    def test_empty_window_is_happy(self) -> None:
        assert OutcomeWindow().mood == Mood.HAPPY

    def test_empty_window_resolves_happy_tags(self) -> None:
        assert OutcomeWindow().resolve_tags() == "[happy]"

    def test_productive_all_ok_session_is_happy_not_frustrated(self) -> None:
        # The exact inversion of the "always frustrated" bug: a long clean run
        # is happy, not frustrated.
        window = _window(*["ok"] * 12)
        assert window.mood == Mood.HAPPY


class TestFailDeepens:
    """Each consecutive fail raises the run by at most 1 and never eases the mood."""

    def test_run_increments_by_at_most_one_and_never_lowers_mood(self) -> None:
        window = OutcomeWindow()
        prev_run = window.run_fail
        prev_rank = _MOOD_RANK[window.mood]
        for _ in range(_MAX + 5):
            window.record(Outcome.FAIL)
            assert window.run_fail >= prev_run or prev_run == _MAX
            assert window.run_fail <= prev_run + 1
            assert _MOOD_RANK[window.mood] >= prev_rank
            prev_run = window.run_fail
            prev_rank = _MOOD_RANK[window.mood]

    def test_first_fail_from_clean_is_focused(self) -> None:
        window = _window("ok", "ok")
        window.record(Outcome.FAIL)
        assert window.mood == Mood.FOCUSED


class TestOkResetsRun:
    """Any ok makes the trailing run zero and lands happy or relieved."""

    def test_ok_zeroes_the_run(self) -> None:
        window = _window("fail", "fail", "fail")
        window.record(Outcome.OK)
        assert window.run_fail == 0

    def test_ok_after_fails_is_never_negative_mood(self) -> None:
        window = _window("fail", "fail", "fail", "fail", "fail")
        window.record(Outcome.OK)
        assert window.mood in (Mood.HAPPY, Mood.RELIEVED)


class TestOkRelievedIffRecent:
    """ok is relieved iff a fail sits within the last recent_k outcomes, else happy."""

    def test_ok_right_after_fail_is_relieved(self) -> None:
        assert _window("fail", "ok").mood == Mood.RELIEVED

    def test_ok_decays_to_happy_after_recent_k_clean(self) -> None:
        # recent_k == 3: the third clean command pushes the fail out of memory.
        assert _window("fail", "ok", "ok").mood == Mood.RELIEVED
        assert _window("fail", "ok", "ok", "ok").mood == Mood.HAPPY

    def test_ok_with_no_recent_fail_is_happy(self) -> None:
        assert _window("ok", "ok", "ok", "ok").mood == Mood.HAPPY


class TestRunBandsThroughWindow:
    """Trailing-run bands read through a real window: 1-2 focused, 3-4 frustrated."""

    @pytest.mark.parametrize(("fails", "mood"), [(1, Mood.FOCUSED), (2, Mood.FOCUSED)])
    def test_focused(self, fails: int, mood: Mood) -> None:
        assert _window("ok", *["fail"] * fails).mood == mood

    @pytest.mark.parametrize(
        ("fails", "mood"), [(3, Mood.FRUSTRATED), (4, Mood.FRUSTRATED)]
    )
    def test_frustrated(self, fails: int, mood: Mood) -> None:
        assert _window("ok", *["fail"] * fails).mood == mood

    @pytest.mark.parametrize("fails", [5, 6])
    def test_weary(self, fails: int) -> None:
        assert _window("ok", *["fail"] * fails).mood == Mood.WEARY


class TestFifoEvictionBenign:
    """FIFO eviction can only move the mood toward recovery, never deeper."""

    def test_full_window_of_fails_is_weary(self) -> None:
        window = _window(*["fail"] * _MAX)
        assert len(window) == _MAX
        assert window.mood == Mood.WEARY

    def test_eviction_never_deepens_beyond_weary(self) -> None:
        window = _window(*["fail"] * _MAX)
        window.record(Outcome.FAIL)  # evicts the oldest fail
        assert len(window) == _MAX
        assert window.run_fail == _MAX
        assert window.mood == Mood.WEARY

    def test_record_evicts_oldest_at_capacity(self) -> None:
        window = _window(*["ok"] * _MAX)
        window.record(Outcome.FAIL)
        assert len(window) == _MAX
        assert window.serialize().split(",")[-1] == "fail"
        assert window.serialize().split(",").count("ok") == _MAX - 1
