"""Tests for the auto-vibe cadence logic (src/punt_vox/vibe_nudge.py)."""

from __future__ import annotations

import pytest

from punt_vox.vibe_nudge import (
    DEFAULT_THRESHOLD,
    VIBE_NUDGE_REMINDER,
    NudgeDecision,
    VibeNudge,
)


class TestVibeNudgeAdvance:
    def test_manual_leaves_counter_and_stays_silent(self) -> None:
        decision = VibeNudge().advance(mode="manual", turns=3)
        assert decision == NudgeDecision(next_turns=3, reminder=None)

    def test_off_leaves_counter_and_stays_silent(self) -> None:
        decision = VibeNudge().advance(mode="off", turns=0)
        assert decision == NudgeDecision(next_turns=0, reminder=None)

    def test_auto_below_threshold_increments_silently(self) -> None:
        decision = VibeNudge().advance(mode="auto", turns=0)
        assert decision.next_turns == 1
        assert decision.reminder is None

    def test_auto_reminder_lands_on_nth_prompt_not_before(self) -> None:
        nudge = VibeNudge()
        turns = 0
        for _ in range(DEFAULT_THRESHOLD - 1):
            decision = nudge.advance(mode="auto", turns=turns)
            assert decision.reminder is None
            turns = decision.next_turns
        fired = nudge.advance(mode="auto", turns=turns)
        assert fired.reminder == VIBE_NUDGE_REMINDER
        assert fired.next_turns == 0

    def test_custom_threshold_fires_at_custom_count(self) -> None:
        nudge = VibeNudge(threshold=2)
        first = nudge.advance(mode="auto", turns=0)
        assert first.reminder is None
        assert first.next_turns == 1
        second = nudge.advance(mode="auto", turns=first.next_turns)
        assert second.reminder == VIBE_NUDGE_REMINDER
        assert second.next_turns == 0

    def test_threshold_below_one_rejected(self) -> None:
        with pytest.raises(ValueError, match="threshold must be at least 1"):
            VibeNudge(threshold=0)
