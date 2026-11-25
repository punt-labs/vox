"""Configure CommonMark parsing for speech source spans."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Self, final, override

from markdown_it import MarkdownIt
from markdown_it.rules_block import fence
from markdown_it.rules_inline import image, link

from punt_vox._link_labels import VIBE_TAG_RE, LinkSpan as LinkSpan

if TYPE_CHECKING:
    from collections.abc import Callable

    from markdown_it.rules_block import StateBlock
    from markdown_it.rules_inline import StateInline

ALT_OPENING = "vox_alt_opening"
SOURCE_OFFSET = "vox_source_offset"
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
            "link", lambda state, silent: cls._label_rule(link, state, silent=silent)
        )
        parser.inline.ruler.at(
            "image", lambda state, silent: cls._label_rule(image, state, silent=silent)
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
    def _label_rule(
        rule: Callable[[StateInline, bool], bool], state: StateInline, *, silent: bool
    ) -> bool:
        opening = state.pos + (rule is image)
        opening_token = len(state.tokens) + bool(state.pending)
        matched = rule(state, silent)
        if matched and not silent:
            label_end = state.md.helpers.parseLinkLabel(
                state, opening, disableNested=rule is link
            )
            token = state.tokens[opening_token]
            if rule is image:
                token.meta[ALT_OPENING] = opening + 1
            if state.pos > label_end + 2 and state.src[label_end + 1] == "(":
                token.meta[SOURCE_OFFSET] = (opening, label_end, state.pos - 1)
        return matched

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
