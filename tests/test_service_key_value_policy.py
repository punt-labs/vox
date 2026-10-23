"""Tests for punt_vox.service.key_value_policy -- what a keys.env value may hold."""

from __future__ import annotations

import pytest

from punt_vox.service.key_value_policy import Refusal, ValuePolicy


@pytest.mark.parametrize("value", ["sk-abc", "AKIA0123456789", "!~#$%^&*()"])
def test_header_safe_accepts_printable_non_space_ascii(value: str) -> None:
    assert ValuePolicy.header_safe().refusal(value) is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param("a b", Refusal("a space", 2, "no HTTP header may carry it")),
        pytest.param("ab\n", Refusal("a line break", 3, "no HTTP header may carry it")),
        pytest.param(
            "\x00",
            Refusal("a non-printable character", 1, "no HTTP header may carry it"),
        ),
        pytest.param(
            "ab\u00e9",
            Refusal("a non-ASCII character", 3, "no HTTP header may carry it"),
        ),
    ],
)
def test_header_safe_reports_the_first_bad_character(
    value: str, expected: Refusal
) -> None:
    assert ValuePolicy.header_safe().refusal(value) == expected


@pytest.mark.parametrize("value", ["my profile", "eu-west-1", "caf\u00e9", "sk-abc"])
def test_printable_accepts_spaces_and_non_ascii(value: str) -> None:
    assert ValuePolicy.printable().refusal(value) is None


@pytest.mark.parametrize(
    ("value", "char_class"),
    [
        pytest.param("a\u2028b", "a line break", id="line-separator"),
        pytest.param("a\x85b", "a line break", id="next-line"),
        pytest.param("a\tb", "a non-printable character", id="tab"),
        pytest.param("a\u200bb", "a non-printable character", id="zero-width-space"),
    ],
)
def test_printable_refuses_line_breaks_and_controls(
    value: str, char_class: str
) -> None:
    refusal = ValuePolicy.printable().refusal(value)
    assert refusal == Refusal(char_class, 2, "it could start a new keys.env line")


def test_refusal_renders_class_position_and_rule() -> None:
    refusal = Refusal("a space", 4, "no HTTP header may carry it")
    assert str(refusal) == "a space at position 4 (no HTTP header may carry it)"


def test_refusal_at_never_keeps_the_character() -> None:
    refusal = Refusal.at("ab\u201c", 2, "rule")
    assert "\u201c" not in str(refusal)
    assert refusal == Refusal("a non-ASCII character", 3, "rule")
