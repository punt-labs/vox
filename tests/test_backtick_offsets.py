"""CommonMark backtick locations in raw text."""

from punt_vox._backtick_offsets import BacktickOffsets


def test_match_backticks_after_block_quote_prefixes_and_crlf() -> None:
    text = "> Use `one\r\n> two` now"
    content = "Use `one\ntwo` now"
    lines = BacktickOffsets.line_boundaries(text)
    assert BacktickOffsets.from_content(text, lines, content) == {
        content.index("`"): text.index("`"),
        content.rindex("`"): text.rindex("`"),
    }
