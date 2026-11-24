"""Types returned by provider voice roster listings."""

from __future__ import annotations

from typing import TypedDict

__all__ = ["FeaturedVoice"]


class FeaturedVoice(TypedDict):
    """An advertised provider voice with a curated description."""

    name: str
    blurb: str
