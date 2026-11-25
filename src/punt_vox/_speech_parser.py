"""Configure CommonMark parsing for speech source spans."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Self, final, override

from markdown_it import MarkdownIt
from markdown_it.rules_block import fence
from markdown_it.rules_inline import image, link

from punt_vox._speech_label_rule import (
    ALT_OPENING as ALT_OPENING,
    SOURCE_OFFSET as SOURCE_OFFSET,
    VIBE_TAG_RE,
    LabelBounds as LabelBounds,
    LinkSpan as LinkSpan,
    SpeechLabelRule,
)

if TYPE_CHECKING:
    from markdown_it.rules_block import StateBlock

_VIBED = "vox_vibed"
_FENCE_TAGS = re.compile(rf"(?:{VIBE_TAG_RE.pattern}[ \t]*)+(?=`{{3,}}|~{{3,}})")


@final
class SpeechParser(MarkdownIt):
    """Parse speech Markdown without opening link destinations."""

    @classmethod
    def create(cls) -> Self:
        """Install source-offset tracking and expressive fence recognition."""
        parser = cls("commonmark")
        parser.block.ruler.at(
            "fence",
            lambda state, start, end, silent: cls._fence_rule(
                state, start, end, silent=silent
            ),
            {"alt": ["paragraph", "reference", "blockquote", "list"]},
        )
        parser.inline.ruler.at(
            "link",
            lambda state, silent: SpeechLabelRule.apply(link, state, silent=silent),
        )
        parser.inline.ruler.at(
            "image",
            lambda state, silent: SpeechLabelRule.apply(image, state, silent=silent),
        )
        return parser

    @staticmethod
    def speech_env(*, vibed: bool) -> dict[str, bool]:
        """Configure whether expressive tags may precede code fences."""
        return {_VIBED: vibed}

    @override
    def validateLink(self, url: str) -> bool:
        """Allow local file links while keeping the parser's other URL rules."""
        return url.lower().startswith("file:") or super().validateLink(url)

    @staticmethod
    def _fence_rule(
        state: StateBlock, start_line: int, end_line: int, *, silent: bool
    ) -> bool:
        if not state.env.get(_VIBED):
            return fence(state, start_line, end_line, silent)
        shift = state.tShift[start_line]
        start = state.bMarks[start_line] + shift
        tags = _FENCE_TAGS.match(state.src, start, state.eMarks[start_line])
        if tags is None:
            return fence(state, start_line, end_line, silent)
        state.tShift[start_line] += tags.end() - start
        try:
            return fence(state, start_line, end_line, silent)
        finally:
            state.tShift[start_line] = shift
