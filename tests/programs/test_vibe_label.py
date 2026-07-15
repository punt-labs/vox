"""Tests for the bounded, tag-safe vibe label a session mood collapses to."""

from __future__ import annotations

import pytest

from punt_vox.voxd.programs.vibe_label import VibeLabel

_PATHOLOGICAL_MOOD = (
    "a long brutal grind that shipped: 6 review rounds — 4.12.0 out the "
    "door — and this very nudge just proved itself firing live"
)


class TestValue:
    def test_long_mood_is_capped_and_sanitized(self) -> None:
        value = VibeLabel(_PATHOLOGICAL_MOOD).value
        assert len(value) <= 48
        assert ":" not in value
        assert "—" not in value
        assert value.startswith("a long brutal grind")

    def test_control_characters_collapse_to_a_single_space(self) -> None:
        assert VibeLabel("line one\n\tline two").value == "line one line two"

    def test_interior_whitespace_collapses(self) -> None:
        assert VibeLabel("late   night     flow").value == "late night flow"

    def test_surrounding_whitespace_is_trimmed(self) -> None:
        assert VibeLabel("   focused calm   ").value == "focused calm"

    def test_short_mood_is_preserved(self) -> None:
        assert VibeLabel("calm").value == "calm"

    def test_safe_punctuation_is_kept(self) -> None:
        assert VibeLabel("late-night, 4.12.0 flow").value == "late-night, 4.12.0 flow"

    def test_unicode_letters_are_preserved(self) -> None:
        assert VibeLabel("néon 夜").value == "néon 夜"

    def test_punctuation_only_mood_is_empty(self) -> None:
        assert VibeLabel("???:::!!!").value == ""

    def test_empty_mood_is_empty(self) -> None:
        assert VibeLabel("").value == ""

    def test_whitespace_only_mood_is_empty(self) -> None:
        assert VibeLabel("   \n\t ").value == ""

    def test_normalization_is_idempotent(self) -> None:
        once = VibeLabel(_PATHOLOGICAL_MOOD).value
        assert VibeLabel(once).value == once

    def test_word_boundary_cap_keeps_whole_words(self) -> None:
        # Capping never leaves a dangling partial word.
        value = VibeLabel("a b c " + "word " * 20).value
        assert not value.endswith("wor")
        assert value == value.rstrip()


class TestTruthiness:
    def test_populated_label_is_truthy(self) -> None:
        assert bool(VibeLabel("calm")) is True

    def test_empty_label_is_falsy(self) -> None:
        assert bool(VibeLabel("!!!")) is False


class TestNameSegment:
    def test_slug_is_lowercase_hyphenated(self) -> None:
        assert VibeLabel("Focused Calm").name_segment(32) == "focused-calm"

    def test_empty_label_yields_empty_segment(self) -> None:
        assert VibeLabel("???").name_segment(32) == ""

    def test_segment_is_capped_on_a_hyphen_boundary(self) -> None:
        segment = VibeLabel(_PATHOLOGICAL_MOOD).name_segment(32)
        assert len(segment) <= 32
        assert not segment.startswith("-")
        assert not segment.endswith("-")
        assert "--" not in segment

    def test_unicode_slug_is_ascii_safe(self) -> None:
        segment = VibeLabel("néon 夜 flow").name_segment(32)
        assert segment.isascii()
        assert all(char.islower() or char.isdigit() or char == "-" for char in segment)


class TestValueObject:
    def test_equality_is_by_bounded_value(self) -> None:
        assert VibeLabel("calm ") == VibeLabel("  calm")
        assert VibeLabel("calm") != VibeLabel("energetic")

    def test_hash_matches_equal_labels(self) -> None:
        assert hash(VibeLabel("calm ")) == hash(VibeLabel("calm"))

    def test_not_equal_to_foreign_type(self) -> None:
        assert VibeLabel("calm") != "calm"

    def test_repr_and_str(self) -> None:
        label = VibeLabel("calm")
        assert repr(label) == "VibeLabel('calm')"
        assert str(label) == "calm"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("calm", "calm"),
        ("  Focus  Beats  ", "Focus Beats"),
        ("!!!", ""),
        ("a/b:c|d", "a b c d"),
    ],
)
def test_value_table(raw: str, expected: str) -> None:
    assert VibeLabel(raw).value == expected
