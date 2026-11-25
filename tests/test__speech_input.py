"""Tests for the comparison rewrite before token normalization."""

from __future__ import annotations

from punt_vox._speech_input import SpeechInput


def test_preserves_comparison_spacing_and_quote_prefixes() -> None:
    text = "> x\t<  3\r\n1) > > 2\nx <\u20283"
    assert SpeechInput.prepare(text) == "> x\tless than  3\r\n1) > > 2\nx <\u20283"


def test_preserves_long_runs_of_vibe_tags() -> None:
    tags = "[warm] " * 4_000
    assert SpeechInput.prepare(f"x {tags}< 3", vibed=True) == f"x {tags}less than 3"
    assert SpeechInput.prepare(f"{tags}x!", vibed=True) == f"{tags}x!"


def test_requires_a_valid_operand_before_spelling_a_bare_angle() -> None:
    assert SpeechInput.prepare("... < 3") == "... < 3"
