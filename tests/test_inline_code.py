"""Inline span boundaries before speech tokenization."""

from punt_vox._inline_code import InlineCode


def test_transform_keeps_fences_and_delimiters() -> None:
    text = "```\n`code`\n\n```\n\nUse ``one ` two`` and `three`.\n"
    expected = "```\n`code`\n\n```\n\nUse ``ONE ` TWO`` and `THREE`.\n"
    assert InlineCode.rewrite(text, str.upper) == expected
