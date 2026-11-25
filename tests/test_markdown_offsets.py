"""CommonMark inline marker locations in raw text."""

from punt_vox._markdown_offsets import MarkdownOffsets


def test_match_backticks_after_block_quote_prefixes_and_crlf() -> None:
    text = "> Use `one\r\n> two` now"
    content = "Use `one\ntwo` now"
    lines = MarkdownOffsets.line_boundaries(text)
    assert MarkdownOffsets.from_content(text, lines, content) == {
        content.index("`"): text.index("`"),
        content.rindex("`"): text.rindex("`"),
    }
