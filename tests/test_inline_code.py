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


def test_adjacent_spans_do_not_rescan_the_same_token() -> None:
    class CountedText(str):
        read = 0

        def __getitem__(self, key: int | slice) -> str:
            piece = super().__getitem__(key)
            self.read += len(piece)
            return piece

    spans = "x`$1`"
    text = CountedText("/tmp/" + spans * 100)
    assert InlineCode.rewrite_currency(text) == text
    assert text.read < 10 * len(text)
