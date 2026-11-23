"""Inline span boundaries before speech tokenization."""

from punt_vox._inline_code import InlineCode


def test_transform_keeps_fences_and_delimiters() -> None:
    text = "```\n`code`\n\n```\n\nUse ``one ` two`` and `three`.\n"
    expected = "```\n`code`\n\n```\n\nUse ``ONE ` TWO`` and `THREE`.\n"
    assert InlineCode.rewrite(text, str.upper) == expected


def test_backticks_in_html_attributes_do_not_open_code() -> None:
    text = '<span title="`x"> use `$1`'
    assert InlineCode.rewrite(text, lambda _: "literal") == (
        '<span title="`x"> use `literal`'
    )


def test_span_offsets_survive_leading_unicode_whitespace_lines() -> None:
    text = "\N{NO-BREAK SPACE}\nUse `code`"
    assert InlineCode.rewrite(text, str.upper) == "\N{NO-BREAK SPACE}\nUse `CODE`"
