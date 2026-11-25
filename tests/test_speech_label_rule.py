"""Direct tests of inline Markdown label metadata."""

from __future__ import annotations

import pytest
from markdown_it import MarkdownIt
from markdown_it.rules_inline import StateInline, image

from punt_vox._speech_label_rule import (
    ALT_OPENING,
    SOURCE_OFFSET,
    LabelBounds,
    SpeechLabelRule,
)


@pytest.mark.parametrize(
    ("limit", "expected"),
    [(19, LabelBounds(2, 10, 18, 2)), (18, LabelBounds(2, 10, 17, 1))],
)
def test_image_wrapper_is_only_included_within_inline_limit(
    limit: int, expected: LabelBounds
) -> None:
    state = StateInline("[![diagram](d.png)]", MarkdownIt("commonmark"), {}, [])
    state.pos = 1
    state.posMax = limit

    assert SpeechLabelRule.apply(image, state, silent=False)
    assert state.tokens[0].meta[ALT_OPENING] == 3
    assert state.tokens[0].meta[SOURCE_OFFSET] == expected


def test_silent_image_match_does_not_emit_metadata() -> None:
    state = StateInline("![diagram](d.png)", MarkdownIt("commonmark"), {}, [])

    assert SpeechLabelRule.apply(image, state, silent=True)
    assert not state.tokens
