"""CommonMark link syntax in spoken text."""

from __future__ import annotations

import pytest

from punt_vox._inline_code import InlineCode
from punt_vox._markdown_offsets import MarkdownOffsets


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
        ("See ![guide](/cover.png).", "See ![guide](/cover.png)."),
        ("See ![the [guide](/help)](/cover.png).", "See ![the guide](/cover.png)."),
    ],
)
def test_link_labels(raw: str, expected: str) -> None:
    assert InlineCode.rewrite_currency(raw, vibed=True) == expected


def test_leaves_link_intact_if_source_offsets_are_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing_offsets(*_: object) -> dict[int, int]:
        return {}

    monkeypatch.setattr(MarkdownOffsets, "from_content", missing_offsets)
    raw = "Read [the guide](/help)."
    assert InlineCode.rewrite_currency(raw) == raw
