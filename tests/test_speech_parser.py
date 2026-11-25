"""Tests for the CommonMark speech token metadata."""

from __future__ import annotations

import pytest

from punt_vox._speech_parser import ALT_OPENING, SOURCE_OFFSET, SpeechParser


@pytest.mark.parametrize(
    ("text", "image_bounds"),
    [
        ("See ![chart](image.png).", (5, 11, 22, 1)),
        ("See ![chart][img].\n\n[img]: image.png", None),
    ],
)
def test_image_records_alt_opening_and_only_inline_destination_bounds(
    text: str, image_bounds: tuple[int, int, int, int] | None
) -> None:
    tokens = SpeechParser.create().parse(text, SpeechParser.speech_env(vibed=False))
    inline = next(token for token in tokens if token.type == "inline")
    assert inline.children is not None
    image = next(token for token in inline.children if token.type == "image")
    assert image.meta[ALT_OPENING] == 6
    assert image.meta.get(SOURCE_OFFSET) == image_bounds


@pytest.mark.parametrize(
    ("vibed", "kind"), [(True, "fence"), (False, "paragraph_open")]
)
def test_expressive_fence_uses_parse_environment(*, vibed: bool, kind: str) -> None:
    tokens = SpeechParser.create().parse(
        "[whispering] ```\ncode\n```", SpeechParser.speech_env(vibed=vibed)
    )
    assert tokens[0].type == kind
