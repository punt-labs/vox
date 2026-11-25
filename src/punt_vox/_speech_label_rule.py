"""Record inline Markdown label bounds for speech projection."""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple, final

from markdown_it.common.utils import unescapeAll
from markdown_it.rules_inline import image

from punt_vox._link_labels import VIBE_TAG_RE as VIBE_TAG_RE, LinkSpan as LinkSpan

if TYPE_CHECKING:
    from collections.abc import Callable

    from markdown_it.rules_inline import StateInline
    from markdown_it.token import Token

ALT_OPENING = "vox_alt_opening"
SOURCE_OFFSET = "vox_source_offset"


class LabelBounds(NamedTuple):
    opening: int
    label_end: int
    close: int
    prefix_width: int


@final
class SpeechLabelRule:
    """Identify source bounds for CommonMark link labels and image alts."""

    @classmethod
    def apply(
        cls,
        rule: Callable[[StateInline, bool], bool],
        state: StateInline,
        *,
        silent: bool,
    ) -> bool:
        start = state.pos
        opening_token = len(state.tokens) + bool(state.pending)
        matched = rule(state, silent)
        if matched and not silent:
            cls._record(
                state, state.tokens[opening_token], start, is_image=rule is image
            )
        return matched

    @classmethod
    def _record(
        cls, state: StateInline, token: Token, start: int, *, is_image: bool
    ) -> None:
        opening = start + is_image
        label_end = state.md.helpers.parseLinkLabel(
            state, opening, disableNested=not is_image
        )
        if is_image:
            token.meta[ALT_OPENING] = opening + 1
        if state.pos > label_end + 2 and state.src[label_end + 1] == "(":
            wrappers = cls._wrappers(state, start, label_end) if is_image else 0
            token.meta[SOURCE_OFFSET] = LabelBounds(
                opening, label_end, state.pos - 1 + wrappers, is_image + wrappers
            )

    @staticmethod
    def _wrappers(state: StateInline, start: int, label_end: int) -> int:
        # An image alone in literal brackets must not become an expressive cue.
        if not VIBE_TAG_RE.fullmatch(
            f"[{unescapeAll(state.src[start + 2 : label_end])}]"
        ):
            return 0
        left, right = start - 1, state.pos
        while (
            left >= 0
            and right < state.posMax
            and state.src[left] == "["
            and state.src[right] == "]"
        ):
            left -= 1
            right += 1
        return right - state.pos
