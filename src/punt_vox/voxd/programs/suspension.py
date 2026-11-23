"""``PlaybackSuspension`` -- the daemon's click-free pause/resume seam.

With mpv, pause is not a teardown and resume is not a re-spawn. Pause is a single
``set_property pause`` on the one persistent process: mpv stops feeding the audio
output and resumes from the exact same decoder position, so there is no underrun
click and no wall-clock seek to reconstruct. The suspension holds the paused flag
and delegates the click-free IPC to the injected :class:`Player`.

The loop reads the effective pause, a user pause or a call hold, at
two points: at the advance decision, as the ``T3`` guard against an in-flight
``eof`` advancing under "paused"; and at load time, to decide whether a reload
loads paused (Fork B / I6) -- for a prev/next while paused and for a post-crash
reload while paused. ``reset`` (a switch or off) clears the user pause; the loop's
``stop`` or next ``loadfile`` settles the mpv side, so ``reset`` issues no IPC.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING, Self, final

if TYPE_CHECKING:
    from collections.abc import Generator

    from punt_vox.voxd.programs.player import Player

__all__ = ["PlaybackSuspension"]


@final
class PlaybackSuspension:
    """Combine user and call pauses and delegate changes to the player."""

    __slots__ = ("_call_held", "_paused", "_player")
    _call_held: bool
    _paused: bool
    _player: Player

    def __new__(cls, player: Player) -> Self:
        """Create a playback suspension."""
        self = super().__new__(cls)
        self._call_held = False
        self._paused = False
        self._player = player
        return self

    @property
    def is_paused(self) -> bool:
        """Return whether the active source is suspended in place (cursor held)."""
        return self._paused or self._call_held

    @property
    def user_paused(self) -> bool:
        """Return the transport pause choice, independent of a call hold."""
        return self._paused

    def pause(self) -> None:
        """Suspend playback where it is -- mpv freezes in place. Idempotent.

        The click-free ``set_property pause`` holds the exact decoder position; no
        teardown, no seek. Dropped by the player when mpv is not ready -- the flag
        still stands, and a post-recovery reload honours it (I6).
        """
        was_paused = self.is_paused
        self._paused = True
        self._sync(was_paused=was_paused)

    def resume(self) -> None:
        """Continue from the frozen position -- mpv un-pauses in place. Idempotent."""
        was_paused = self.is_paused
        self._paused = False
        self._sync(was_paused=was_paused)

    @contextmanager
    def hold_for_call(self) -> Generator[None]:
        """Hold music for the daemon's one call without changing the user pause."""
        was_paused = self.is_paused
        self._call_held = True
        try:
            self._sync(was_paused=was_paused)
            yield
        finally:
            was_paused = self.is_paused
            self._call_held = False
            self._sync(was_paused=was_paused)

    def _sync(self, *, was_paused: bool) -> None:
        paused = self.is_paused
        if was_paused == paused:
            return
        if paused:
            self._player.pause()
        else:
            self._player.resume()

    def reset(self) -> None:
        """Clear the user pause on a switch or off; the loop settles the mpv side.

        A stop or a source switch starts the next source fresh, never inheriting
        a displaced album's pause. The loop's ``stop`` (off) or next ``loadfile``
        with the effective pause (switch) drives mpv, so this issues no IPC.
        """
        self._paused = False
