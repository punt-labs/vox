"""The consume-only replay source -- a rotation cursor over a fixed Selection.

``SelectionPlayback`` is the replay half of :class:`PlaybackSource` (the Z
``Radio``): a shuffle-rotation over a :class:`Selection` that may span albums,
with no cap, no fill, and no generation. Anti-repeat is delegated to the injected
:class:`PlaybackPolicy` -- the same :class:`RotatePolicy` that backs
``Program.rotate`` -- so "no immediate repeat" is defined once. It
generates nothing: :attr:`wants_generation` is structurally ``False``.
"""

from __future__ import annotations

from typing import Self, final

from punt_vox.voxd.programs.part import Part
from punt_vox.voxd.programs.playback_policy import Advance, PlaybackPolicy
from punt_vox.voxd.programs.selection import Selection

__all__ = ["SelectionPlayback"]


@final
class SelectionPlayback:
    """A cursor over a Selection: begins at the first track, rotates on advance."""

    __slots__ = ("_last_played", "_playing", "_policy", "_selection")
    _selection: Selection
    _policy: PlaybackPolicy
    _playing: Part | None
    _last_played: Part | None

    def __new__(cls, selection: Selection, policy: PlaybackPolicy) -> Self:
        self = super().__new__(cls)
        self._selection = selection
        self._policy = policy
        pool = selection.playable_pool()
        self._playing = pool[0] if pool else None
        self._last_played = None
        return self

    @property
    def selection(self) -> Selection:
        """Return the Selection this cursor replays."""
        return self._selection

    @property
    def playing(self) -> Part | None:
        """Return the (selection-unique) Part currently playing, or ``None``."""
        return self._playing

    @property
    def last_played(self) -> Part | None:
        """Return the Part played immediately before, or ``None``."""
        return self._last_played

    def rotate(self) -> None:
        """Advance to another member of the Selection (auto-advance, next, skip).

        Reuses the injected policy's anti-repeat rule; a singleton selection
        replays its sole member. An empty selection is a caught boundary -- the
        cursor holds ``None`` and rotate is a no-op, never a crash (mirrors the
        empty-pool guard).
        """
        pool = self._selection.playable_pool()
        if not pool:
            return
        result = self._policy.next_part(pool, self._playing)
        if not isinstance(result, Advance):
            msg = "selection policy signalled COMPLETE, which a radio has no end for"
            raise AssertionError(msg)
        self._last_played = self._playing
        self._playing = result.part

    @property
    def wants_generation(self) -> bool:
        """A Selection never generates -- structurally ``False``."""
        return False

    @property
    def advances_on_end(self) -> bool:
        """Whether the loop should auto-advance this radio on a track end."""
        return self._playing is not None
