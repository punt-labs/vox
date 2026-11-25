"""Tests for spelling currency signs in inline code literally."""

from __future__ import annotations

import pytest

from punt_vox._code_currency import CodeCurrency


@pytest.mark.parametrize(
    ("text", "in_path", "spoken"),
    [
        ("$5 costs €3", False, "dollar sign 5 costs euro sign 3"),
        ("~/bin/$5 $5", True, "~/bin/$5 dollar sign 5"),
        ("$HOME/bin", False, "$HOME/bin"),
    ],
)
def test_spell_says_signs_literally_outside_paths(
    text: str, in_path: bool, spoken: str
) -> None:
    assert CodeCurrency.spell(text, in_path=in_path) == spoken


@pytest.mark.parametrize(
    ("text", "is_path"),
    [("./run.sh", True), ("`~/bin", True), ("$HOME/bin", True), ("$5", False)],
)
def test_is_code_path(text: str, is_path: bool) -> None:
    assert CodeCurrency.is_code_path(text) is is_path
