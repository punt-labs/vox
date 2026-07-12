"""Tests for the mood derivation (src/punt_vox/vibe_mood.py).

Tests are named after the schemas in the Z model docs/vibe-exit-code.tex so the
code and the formal model stay tied together.
"""

from __future__ import annotations

import pytest

from punt_vox.vibe_mood import DEFAULT_THRESHOLDS, Mood, MoodThresholds


class TestMoodTags:
    """Each mood maps to its ElevenLabs expressive tags."""

    def test_tag_mapping(self) -> None:
        assert Mood.HAPPY.tags == "[happy]"
        assert Mood.FOCUSED.tags == "[focused]"
        assert Mood.FRUSTRATED.tags == "[frustrated] [sighs]"
        assert Mood.WEARY.tags == "[weary]"
        assert Mood.RELIEVED.tags == "[relieved]"


class TestForcedInvariants:
    """focus_from == 1 and weary_from < max_window — their violation breaks the model.

    The formal model proves both are load-bearing: focus_from must be exactly 1
    for the derivation to stay total, and weary_from must sit below max_window or
    FIFO eviction masks weary. Construction enforces both.
    """

    def test_default_thresholds_satisfy_invariants(self) -> None:
        assert DEFAULT_THRESHOLDS.focus_from == 1
        assert DEFAULT_THRESHOLDS.weary_from < DEFAULT_THRESHOLDS.max_window

    def test_focus_from_zero_rejected(self) -> None:
        with pytest.raises(ValueError, match="focus_from must be 1"):
            MoodThresholds(
                focus_from=0, frust_from=3, weary_from=5, recent_k=3, max_window=20
            )

    def test_focus_from_two_rejected(self) -> None:
        with pytest.raises(ValueError, match="focus_from must be 1"):
            MoodThresholds(
                focus_from=2, frust_from=3, weary_from=5, recent_k=3, max_window=20
            )

    def test_weary_from_at_or_above_max_window_rejected(self) -> None:
        with pytest.raises(ValueError, match="must be below"):
            MoodThresholds(
                focus_from=1, frust_from=3, weary_from=20, recent_k=3, max_window=20
            )

    def test_unordered_bands_rejected(self) -> None:
        with pytest.raises(ValueError, match="focus_from < frust_from < weary_from"):
            MoodThresholds(
                focus_from=1, frust_from=5, weary_from=3, recent_k=3, max_window=20
            )

    def test_recent_k_below_one_rejected(self) -> None:
        with pytest.raises(ValueError, match="recent_k must be at least 1"):
            MoodThresholds(
                focus_from=1, frust_from=3, weary_from=5, recent_k=0, max_window=20
            )


class TestDefaultHappy:
    """run == 0 with no recent fail derives happy — never a default frustrated."""

    def test_run_zero_no_recent_is_happy(self) -> None:
        assert DEFAULT_THRESHOLDS.mood_for(0, recent_fail=False) == Mood.HAPPY


class TestOkRelievedIffRecent:
    """run == 0 splits on recency: relieved iff a fail is recent, else happy."""

    def test_run_zero_recent_is_relieved(self) -> None:
        assert DEFAULT_THRESHOLDS.mood_for(0, recent_fail=True) == Mood.RELIEVED

    def test_run_zero_not_recent_is_happy(self) -> None:
        assert DEFAULT_THRESHOLDS.mood_for(0, recent_fail=False) == Mood.HAPPY


class TestThresholdBands:
    """The half-open run bands: [1,3) focused, [3,5) frustrated, [5,inf) weary."""

    @pytest.mark.parametrize("run", [1, 2])
    def test_focused_band(self, run: int) -> None:
        assert DEFAULT_THRESHOLDS.mood_for(run, recent_fail=True) == Mood.FOCUSED

    @pytest.mark.parametrize("run", [3, 4])
    def test_frustrated_band(self, run: int) -> None:
        assert DEFAULT_THRESHOLDS.mood_for(run, recent_fail=True) == Mood.FRUSTRATED

    @pytest.mark.parametrize("run", [5, 6, 20])
    def test_weary_band(self, run: int) -> None:
        assert DEFAULT_THRESHOLDS.mood_for(run, recent_fail=True) == Mood.WEARY

    def test_boundary_two_is_last_focused(self) -> None:
        assert DEFAULT_THRESHOLDS.mood_for(2, recent_fail=True) == Mood.FOCUSED

    def test_boundary_three_is_first_frustrated(self) -> None:
        assert DEFAULT_THRESHOLDS.mood_for(3, recent_fail=True) == Mood.FRUSTRATED

    def test_boundary_four_is_last_frustrated(self) -> None:
        assert DEFAULT_THRESHOLDS.mood_for(4, recent_fail=True) == Mood.FRUSTRATED

    def test_boundary_five_is_first_weary(self) -> None:
        assert DEFAULT_THRESHOLDS.mood_for(5, recent_fail=True) == Mood.WEARY
