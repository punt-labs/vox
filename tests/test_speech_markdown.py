"""CommonMark link syntax in spoken text."""

import pytest

from punt_vox._speech_markdown import SpeechMarkdown


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('Read [the guide](<guide (old)> "title").', "Read the guide."),
        ("See [the guide](doc(section)) and [help](/help).", "See the guide and help."),
        ("Open [the notes](file:///home/u/notes.md).", "Open the notes."),
        ("> Read [the guide](\r\n> /help).", "> Read the guide."),
        ("Read [the `guide`](/help).", "Read the `guide`."),
        ("Write `[guide](/help)` literally.", "Write `[guide](/help)` literally."),
        (r"Write \[guide](/help) literally.", r"Write \[guide](/help) literally."),
        ("Keep [guide](/help as typed.", "Keep [guide](/help as typed."),
        ("See ![guide](/cover.png).", "See ![guide](/cover.png)."),
        ("See ![the [guide](/help)](/cover.png).", "See ![the guide](/cover.png)."),
    ],
)
def test_link_labels(raw: str, expected: str) -> None:
    assert SpeechMarkdown.link_labels(raw, vibed=True) == expected
