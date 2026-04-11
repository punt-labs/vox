"""Tests for vibe-to-prompt mapping."""

from __future__ import annotations

import pytest

from punt_vox.music import vibe_to_prompt

# -- Layer 2: time-of-day parametrization -----------------------------------

_TIME_CASES: list[tuple[int, str]] = [
    (6, "late-morning energy, fresh and building momentum"),
    (7, "late-morning energy, fresh and building momentum"),
    (11, "late-morning energy, fresh and building momentum"),
    (12, "afternoon focus, steady and locked in"),
    (14, "afternoon focus, steady and locked in"),
    (16, "afternoon focus, steady and locked in"),
    (17, "evening wind-down, reflective but still moving"),
    (19, "evening wind-down, reflective but still moving"),
    (21, "evening wind-down, reflective but still moving"),
    (22, "late-night deep focus, minimal and hypnotic"),
    (0, "late-night deep focus, minimal and hypnotic"),
    (3, "late-night deep focus, minimal and hypnotic"),
    (5, "late-night deep focus, minimal and hypnotic"),
]

# -- Layer 3: signal intensity parametrization ------------------------------

_SIGNAL_CASES: list[tuple[list[str], str]] = [
    (
        ["git-commit", "git-commit", "git-commit"],
        "productive flow state, things are shipping",
    ),
    (
        ["git-commit"] * 5,
        "productive flow state, things are shipping",
    ),
    (
        ["tests-fail", "tests-fail"],
        "grinding through a tough problem, tension building",
    ),
    (
        ["tests-fail"] * 4,
        "grinding through a tough problem, tension building",
    ),
    (
        [],
        "steady working pace",
    ),
    (
        ["git-commit"],
        "steady working pace",
    ),
    (
        ["tests-fail"],
        "steady working pace",
    ),
    (
        ["other-signal", "other-signal", "other-signal"],
        "steady working pace",
    ),
]

_LOOP_SUFFIX = (
    "Loopable, no distinct intro or outro, smooth ambient texture "
    "that cycles naturally. Driving beat but not overwhelming \u2014 "
    "background music for deep work."
)


class TestTimeOfDay:
    """Layer 2 — hour brackets map to correct phrases."""

    @pytest.mark.parametrize(("hour", "expected"), _TIME_CASES)
    def test_time_bracket(self, hour: int, expected: str) -> None:
        result = vibe_to_prompt(None, None, None, hour, [])
        assert expected in result


class TestWorkIntensity:
    """Layer 3 — signal counts map to correct intensity phrases."""

    @pytest.mark.parametrize(("signals", "expected"), _SIGNAL_CASES)
    def test_signal_intensity(self, signals: list[str], expected: str) -> None:
        result = vibe_to_prompt(None, None, None, 14, signals)
        assert expected in result


class TestCommitTakesPrecedence:
    """When both commit and fail thresholds are met, commits win."""

    def test_commits_over_fails(self) -> None:
        signals = ["git-commit"] * 3 + ["tests-fail"] * 2
        result = vibe_to_prompt(None, None, None, 14, signals)
        assert "productive flow state" in result
        assert "grinding" not in result


class TestStyleMoodFeel:
    """Layer 1 — style, vibe, and vibe_tags assembly."""

    def test_full_layer(self) -> None:
        result = vibe_to_prompt("happy", "[cheerful]", "techno", 14, [])
        assert "techno music" in result
        assert "happy mood" in result
        assert "cheerful feel" in result

    def test_no_style_falls_back_to_ambient(self) -> None:
        result = vibe_to_prompt("happy", None, None, 14, [])
        assert "ambient music" in result
        assert "happy mood" in result

    def test_no_vibe_omits_mood_and_feel(self) -> None:
        result = vibe_to_prompt(None, None, "techno", 14, [])
        assert "techno music" in result
        assert "mood" not in result
        assert "feel" not in result

    def test_no_vibe_no_style(self) -> None:
        result = vibe_to_prompt(None, None, None, 14, [])
        assert "ambient music" in result
        assert "mood" not in result

    def test_vibe_tags_brackets_stripped(self) -> None:
        result = vibe_to_prompt(None, "[calm]", None, 14, [])
        assert "calm feel" in result
        assert "[" not in result.split(". ")[0]

    def test_multi_tag_vibe_tags_cleaned(self) -> None:
        result = vibe_to_prompt(None, "[warm] [satisfied]", None, 14, [])
        assert "warm, satisfied feel" in result
        assert "[" not in result
        assert "]" not in result

    def test_empty_vibe_tags_omitted(self) -> None:
        result = vibe_to_prompt(None, "[]", None, 14, [])
        assert "feel" not in result


class TestFallbacks:
    """Graceful omission when layers have no content."""

    def test_no_style_no_vibe_no_signals(self) -> None:
        result = vibe_to_prompt(None, None, None, 14, [])
        assert result.startswith("ambient music")
        assert "steady working pace" in result
        assert _LOOP_SUFFIX in result

    def test_no_signals(self) -> None:
        result = vibe_to_prompt("focused", "[calm]", "jazz", 10, [])
        assert "steady working pace" in result

    def test_no_vibe(self) -> None:
        result = vibe_to_prompt(None, None, "lo-fi", 22, [])
        assert "lo-fi music" in result
        assert "mood" not in result


class TestCombinedPrompt:
    """Full assembly matches the prototype format from the spec."""

    def test_prototype_example(self) -> None:
        """Reproduce the exact example from the spec."""
        result = vibe_to_prompt(
            "happy",
            "[cheerful]",
            "techno",
            14,
            ["git-commit"] * 3,
        )
        expected = (
            "techno music, happy mood, cheerful feel. "
            "afternoon focus, steady and locked in. "
            "productive flow state, things are shipping. " + _LOOP_SUFFIX
        )
        assert result == expected

    def test_layer_separator(self) -> None:
        """Layers are joined by '. ' — check boundaries between them."""
        result = vibe_to_prompt("calm", None, None, 8, [])
        # Layer 1 -> ". " -> Layer 2
        assert "calm mood. late-morning" in result
        # Layer 2 -> ". " -> Layer 3
        assert "momentum. steady working" in result
        # Layer 3 -> ". " -> Layer 4
        assert "pace. Loopable" in result

    def test_all_layers_present(self) -> None:
        result = vibe_to_prompt(
            "energetic",
            "[bright]",
            "drum-and-bass",
            19,
            ["tests-fail", "tests-fail"],
        )
        assert "drum-and-bass music" in result
        assert "energetic mood" in result
        assert "bright feel" in result
        assert "evening wind-down" in result
        assert "grinding through a tough problem" in result
        assert _LOOP_SUFFIX in result
