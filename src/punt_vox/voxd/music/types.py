"""Domain types for the music subsystem."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

__all__ = [
    "MusicMode",
    "MusicResponse",
    "MusicState",
    "MusicStatus",
]

MusicMode = Literal["off", "on"]
MusicState = Literal["idle", "generating", "playing"]
MusicStatus = Literal["generating", "playing", "stopped", "ignored"]


@dataclass(frozen=True, slots=True)
class MusicResponse:
    """Result of a scheduler domain method."""

    status: MusicStatus
    track: str | None = None
    name: str | None = None
