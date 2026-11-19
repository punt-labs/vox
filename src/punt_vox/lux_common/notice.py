"""Represent status notices shared by the control panel and music player."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Self

__all__ = ["LuxNotice"]


@dataclass(frozen=True, slots=True)
class LuxNotice:
    """A transient scene status: a warning message, or silent (the Null state)."""

    message: str  # the empty string is the silent Null state -- no status line

    @classmethod
    def silent(cls) -> Self:
        """Return the silent notice -- the Null state, rendering no status line."""
        return cls("")

    @classmethod
    def warning(cls, message: str) -> Self:
        """Return a warning notice carrying ``message`` for the scene status line."""
        return cls(message)

    @property
    def is_present(self) -> bool:
        """Return whether a status line should render (a non-empty message)."""
        return bool(self.message)
