"""Playlist pool: choose the next track from a saved (vibe, style) group."""

from __future__ import annotations

import secrets
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Self

__all__ = ["POOL_SIZE", "TrackPool"]

POOL_SIZE = 12


@dataclass(frozen=True, slots=True)
class TrackPool:
    """Immutable view over the saved tracks for one (vibe, style)."""

    _tracks: tuple[Path, ...]

    @classmethod
    def from_paths(cls, paths: Iterable[Path]) -> Self:
        """Build a pool from an enumeration of saved track paths."""
        return cls(tuple(paths))

    def __len__(self) -> int:
        """Return the number of tracks in the pool."""
        return len(self._tracks)

    @property
    def is_full(self) -> bool:
        """Return whether the pool holds enough tracks to rotate."""
        return len(self._tracks) >= POOL_SIZE

    def has_alternative(self, last: Path | None) -> bool:
        """Return whether the pool holds a track other than ``last``.

        False for an empty pool or a single-track pool whose only member is
        ``last`` -- the transient where auto-advance loops the sole track
        until the second one lands.
        """
        return any(t != last for t in self._tracks)

    def pick_next(self, last: Path | None) -> Path:
        """Return a shuffled track, avoiding ``last`` when alternatives exist.

        Raise ``ValueError`` on an empty pool -- callers gate on ``is_full``.
        """
        if not self._tracks:
            msg = "cannot pick a track from an empty pool"
            raise ValueError(msg)
        candidates = tuple(t for t in self._tracks if t != last) or self._tracks
        return secrets.choice(candidates)
