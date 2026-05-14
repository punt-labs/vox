"""Music-specific playback command construction."""

from __future__ import annotations

import platform
from pathlib import Path

__all__ = ["music_player_command"]

_MUSIC_VOLUME = 30


def _is_darwin() -> bool:
    """Return True on macOS."""
    return platform.system() == "Darwin"


def music_player_command(path: Path) -> list[str]:
    """Return the argv for playing a music track at reduced volume.

    Music plays at reduced volume so speech and chimes overlay on top
    at full volume without runtime volume manipulation.
    """
    if _is_darwin():
        return ["afplay", "--volume", "0.3", str(path)]
    return ["ffplay", "-nodisp", "-autoexit", "-volume", str(_MUSIC_VOLUME), str(path)]
