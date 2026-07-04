"""The current playlist: pool identity, background-fill supply, and selection.

A :class:`Playlist` owns one ``(vibe, style)`` pool -- the tracks on disk for
that key, the single :class:`~punt_vox.voxd.music.filler.PoolFiller` that keeps
it topped up, and the pure next-track decision. The scheduler owns a Playlist
and drives its fill from the domain commands; the loop reads its selection to
advance playback. Keeping this separate from :class:`MusicScheduler` splits the
pool/supply concern from session state and command handling (PY-IC-6).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Self

from punt_vox.voxd.music.filler import PoolFiller
from punt_vox.voxd.music.generator import TrackGenerator
from punt_vox.voxd.music.pool import TrackPool

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["Playlist"]


class Playlist:
    """One (vibe, style) pool: its fill supply and next-track selection."""

    __slots__ = ("_filler", "_generator", "_pool_prefix", "_style", "_track", "_vibe")

    _filler: PoolFiller
    _generator: TrackGenerator
    _pool_prefix: str
    _style: str
    _track: Path | None
    _vibe: tuple[str, str]

    def __new__(cls, generator: TrackGenerator) -> Self:
        self = super().__new__(cls)
        self._generator = generator
        self._filler = PoolFiller(generator)
        self._vibe = ("", "")
        self._style = ""
        self._pool_prefix = ""
        self._track = None
        return self

    # -- Pool identity ---------------------------------------------------------

    @property
    def vibe(self) -> tuple[str, str]:
        """Return the current (vibe_text, vibe_tags)."""
        return self._vibe

    @property
    def style(self) -> str:
        """Return the current style."""
        return self._style

    def retune(self, vibe: tuple[str, str], style: str) -> None:
        """Point the playlist at the (vibe, style) pool."""
        self._vibe = vibe
        if style:
            self._style = style
        self._pool_prefix = TrackGenerator.pool_prefix((self._vibe[0], self._style))

    def set_prefix(self, prefix: str) -> None:
        """Point the playlist at a pool identified directly by prefix (replay)."""
        self._pool_prefix = prefix

    def find(self, name: str) -> Path | None:
        """Return the path to a saved track by name, or None."""
        return self._generator.find_track(name)

    # -- Background fill --------------------------------------------------------

    def ensure_fill(self, first_name: str = "") -> None:
        """(Re)start the background fill for the current pool if not full."""
        self._filler.ensure_running(self._vibe, self._style, first_name=first_name)

    def cancel_fill(self) -> None:
        """Cancel the background fill -- no orphaned generation."""
        self._filler.cancel()

    @property
    def filling(self) -> bool:
        """Return whether a background fill task is live."""
        return self._filler.is_running

    async def await_first_track(self) -> Path:
        """Wait for the fill to deliver the first track of an empty pool."""
        return await self._filler.await_first_track()

    # -- Selection -------------------------------------------------------------

    @property
    def is_empty(self) -> bool:
        """Return whether the pool has no tracks on disk yet."""
        return len(self._pool()) == 0

    @property
    def current_track(self) -> Path | None:
        """Return the currently-playing (avoid-repeat) track."""
        return self._track

    def select_first(self) -> Path:
        """Return an initial track from a non-empty pool."""
        return self._pool().pick_next(None)

    def select_next(self) -> Path:
        """Return the next track, avoiding the just-played one.

        Serves both auto-advance and manual skip. On a single-track pool this
        returns that track (the transient loop); once the pool grows it returns
        a different track.
        """
        return self._pool().pick_next(self._track)

    def mark_playing(self, track: Path) -> None:
        """Record ``track`` as the now-playing (avoid-repeat) track."""
        self._track = track

    def clear_current(self) -> None:
        """Forget the avoid-repeat key (a stopped track is not 'just played')."""
        self._track = None

    def _pool(self) -> TrackPool:
        """Return the on-disk pool for the current prefix."""
        return TrackPool.from_paths(self._generator.tracks_for(self._pool_prefix))
