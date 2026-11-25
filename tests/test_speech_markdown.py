"""CommonMark link syntax in spoken text."""

from __future__ import annotations

import pytest
from markdown_it.rules_inline import backtick

from punt_vox._inline_code import InlineCode
from punt_vox._markdown_offsets import MarkdownOffsets
from punt_vox._speech_markdown import SpeechMarkdown


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('Read [the guide](<guide (old)> "title").', "Read the guide."),
        ("See [the guide](doc(section)) and [help](/help).", "See the guide and help."),
        ("Open [the notes](file:///home/u/notes.md).", "Open the notes."),
        ("> Read [the guide](\r\n> /help).", "> Read the guide."),
        ("Read [the `guide`](/help).", "Read the `guide`."),
        (r"Read [A &amp; `R&amp;D`](/help).", "Read A & `R&amp;D`."),
        ("Write `[guide](/help)` literally.", "Write `[guide] (/help)` literally."),
        (r"Write \[guide](/help) literally.", r"Write \[guide](/help) literally."),
        ("Keep [guide](/help as typed.", "Keep [guide](/help as typed."),
        (
            "Use [ref]; see [guide](/help).\n\n[ref]: /reference",
            "Use [ref]; see guide.\n\n[ref]: /reference",
        ),
        ("See ![guide](/cover.png).", "See guide."),
        ("See ![the [guide](/help)](/cover.png).", "See the guide."),
        ('See ![the `guide`](<cover (old).png> "title").', "See the `guide`."),
        ("Write `![guide](/cover.png)`.", "Write `![guide] (/cover.png)`."),
    ],
)
def test_link_labels(raw: str, expected: str) -> None:
    assert InlineCode.rewrite_currency(raw, vibed=True) == expected


@pytest.mark.parametrize("raw", ["Read [the guide](/help).", "Use `$5`."])
def test_leaves_markup_intact_if_source_offsets_are_missing(
    raw: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def missing_offsets(*_: object) -> dict[int, int]:
        return {}

    monkeypatch.setattr(MarkdownOffsets, "from_content", missing_offsets)
    assert InlineCode.rewrite_currency(raw) == raw


def test_missing_code_token_metadata_is_not_an_offset_miss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(SpeechMarkdown, "_code_rule", staticmethod(backtick))
    with pytest.raises(KeyError, match="vox_source_offset"):
        InlineCode.rewrite_currency("Use `$5`.")
