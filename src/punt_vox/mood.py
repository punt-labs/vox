"""Mood classification for vibe-driven chime selection.

Maps free-form vibe strings to one of three tonal families used by
the chime notification system. The same keyword lists are mirrored
in ``hooks/state.sh`` for bash-side classification.

Families:
- **bright**: positive, energetic vibes → pitch-shifted up
- **neutral**: calm/focused or unrecognized → original chimes
- **dark**: frustrated, tense vibes → pitch-shifted down
"""

from __future__ import annotations

MOOD_FAMILIES: dict[str, list[str]] = {
    "bright": [
        "happy",
        "excited",
        "satisfied",
        "warm",
        "playful",
        "cheerful",
        "joyful",
        "energetic",
        "triumphant",
    ],
    "dark": [
        "frustrated",
        "tense",
        "tired",
        "concerned",
        "annoyed",
        "stressed",
        "anxious",
        "overwhelmed",
    ],
}


def classify_mood(vibe: str | None) -> str:
    """Map a free-form vibe string to bright/neutral/dark.

    Matching is case-insensitive substring: ``"feeling happy"`` matches
    the ``happy`` keyword in the bright family. ``None`` or unrecognized
    strings return ``"neutral"``.
    """
    if vibe is None:
        return "neutral"

    lower = vibe.lower()
    for family, keywords in MOOD_FAMILIES.items():
        for keyword in keywords:
            if keyword in lower:
                return family

    return "neutral"
