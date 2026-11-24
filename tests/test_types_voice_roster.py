"""Tests for the provider voice roster entry schema."""

from __future__ import annotations

from typing import get_type_hints

from punt_vox.types_voice_roster import FeaturedVoice


def test_featured_voice_requires_a_name_and_blurb() -> None:
    expected = {"name": str, "blurb": str}
    assert FeaturedVoice.__required_keys__ == expected.keys()
    assert get_type_hints(FeaturedVoice) == expected
