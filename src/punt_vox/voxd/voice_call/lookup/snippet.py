"""One piece of quoted outside text, shaped to be spoken."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

__all__ = ["Snippet"]

_CONTROL: Final = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_SPACE: Final = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class Snippet:
    """A title and a text of outside origin."""

    title: str
    text: str

    @staticmethod
    def clean(text: str, limit: int) -> str:
        """Return ``text`` without control characters or space runs, cut short."""
        return _SPACE.sub(" ", _CONTROL.sub("", text)).strip()[:limit]
