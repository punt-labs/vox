"""Tests for mood classification."""

from __future__ import annotations

import pytest

from punt_vox.mood import MOOD_FAMILIES, classify_mood


class TestClassifyMood:
    """Vibe string → mood family classification."""

    @pytest.mark.parametrize(
        "vibe",
        [
            "happy",
            "excited",
            "satisfied",
            "warm",
            "playful",
            "cheerful",
            "joyful",
            "energetic",
            "triumphant",
        ],
    )
    def test_bright_keywords(self, vibe: str) -> None:
        assert classify_mood(vibe) == "bright"

    @pytest.mark.parametrize(
        "vibe",
        [
            "frustrated",
            "tense",
            "tired",
            "concerned",
            "annoyed",
            "stressed",
            "anxious",
            "overwhelmed",
        ],
    )
    def test_dark_keywords(self, vibe: str) -> None:
        assert classify_mood(vibe) == "dark"

    def test_none_returns_neutral(self) -> None:
        assert classify_mood(None) == "neutral"

    def test_empty_string_returns_neutral(self) -> None:
        assert classify_mood("") == "neutral"

    def test_unrecognized_returns_neutral(self) -> None:
        assert classify_mood("mysterious") == "neutral"

    def test_case_insensitive(self) -> None:
        assert classify_mood("HAPPY") == "bright"
        assert classify_mood("Frustrated") == "dark"

    def test_substring_match(self) -> None:
        assert classify_mood("feeling happy today") == "bright"
        assert classify_mood("a bit stressed out") == "dark"

    def test_first_match_wins(self) -> None:
        """Bright wins when vibe has keywords from both families."""
        result = classify_mood("happy but tired")
        assert result == "bright"


class TestMoodFamilies:
    """Structure validation for MOOD_FAMILIES constant."""

    def test_has_bright_and_dark(self) -> None:
        assert "bright" in MOOD_FAMILIES
        assert "dark" in MOOD_FAMILIES

    def test_no_neutral_family(self) -> None:
        """Neutral is the default — it has no keyword list."""
        assert "neutral" not in MOOD_FAMILIES

    def test_all_keywords_lowercase(self) -> None:
        for family, keywords in MOOD_FAMILIES.items():
            for kw in keywords:
                assert kw == kw.lower(), f"{family} keyword {kw!r} is not lowercase"
