"""The playlist advance strategy -- ``RotatePolicy``.

Realises the playlist ``PlaybackPolicy``: shuffle-advance the pool,
avoiding an immediate repeat when more than one Part is ready, and replaying the
sole Part when only one is. It is today's ``TrackPool.pick_next`` promoted to a
strategy. A playlist has no end, so it never returns ``COMPLETE``; a finite
format (podcast/audiobook) supplies its own sequential policy.
"""

from __future__ import annotations

import secrets
from typing import final

from punt_vox.voxd.programs.part import Part
from punt_vox.voxd.programs.playback_policy import Advance, AdvanceResult

__all__ = ["RotatePolicy"]


@final
class RotatePolicy:
    """Choose the next playlist Part at random, avoiding an immediate repeat."""

    __slots__ = ()

    def next_part(self, pool: tuple[Part, ...], playing: Part | None) -> AdvanceResult:
        """Return a random Part other than ``playing`` (the sole Part replays).

        Raises ``ValueError`` on an empty pool -- ``Program.rotate`` gates on a
        non-empty pool, so this only fires on misuse.
        """
        if not pool:
            msg = "cannot rotate an empty pool"
            raise ValueError(msg)
        alternatives = tuple(part for part in pool if part != playing) or pool
        return Advance(secrets.choice(alternatives))
