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

from punt_vox.voxd.music.filler import FillTarget, PoolFiller
from punt_vox.voxd.music.generator import TrackGenerator
from punt_vox.voxd.music.pool import TrackPool
from punt_vox.voxd.music.prompts import PromptSet

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["Playlist"]


class Playlist:
    """One (vibe, style) pool: its fill supply and next-track selection."""

    __slots__ = (
        "_filler",
        "_generator",
        "_pool_prefix",
        "_prompts",
        "_style",
        "_track",
        "_vibe",
    )

    _filler: PoolFiller
    _generator: TrackGenerator
    _pool_prefix: str
    _prompts: PromptSet | None
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
        self._prompts = None
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
        """Point the playlist at the (vibe, style) pool, dropping stale prompts.

        A pool change invalidates any agent-authored prompts held for the old
        pool; the caller re-installs fresh ones via :meth:`set_prompts` (an
        agent-driven turn-on) or leaves them cleared so the fill falls back to a
        minimal literal prompt (a hook-driven vibe change, no agent in the loop).
        """
        self._vibe = vibe
        if style:
            self._style = style
        self._pool_prefix = TrackGenerator.pool_prefix((self._vibe[0], self._style))
        self._prompts = None

    def set_prefix(self, prefix: str) -> None:
        """Point the playlist at a pool identified directly by prefix (replay)."""
        self._pool_prefix = prefix
        self._prompts = None

    def set_prompts(self, prompts: PromptSet | None) -> None:
        """Install the agent-authored prompts for the current pool (or clear)."""
        self._prompts = prompts

    def find(self, name: str) -> Path | None:
        """Return the path to a saved track by name, or None."""
        return self._generator.find_track(name)

    # -- Background fill --------------------------------------------------------

    def can_generate(self) -> bool:
        """Return whether the pool can be filled (a provider API key is set)."""
        return self._generator.can_generate()

    def ensure_fill(self, first_name: str = "") -> None:
        """(Re)start the background fill for the current pool if not full.

        The fill is keyed on the same ``_pool_prefix`` selection uses, so a
        replayed track's pool is filled -- not the session (vibe, style) pool
        (findings #1/#7). The pool generates from the agent-authored prompts when
        present, or a minimal literal fallback derived from style and mood.
        """
        prompts = self._prompts or PromptSet.fallback(self._style, self._vibe[0])
        target = FillTarget(self._pool_prefix, self._vibe, self._style, prompts)
        self._filler.ensure_running(target, first_name=first_name)

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
    def can_advance(self) -> bool:
        """Return whether a skip/advance has somewhere to go.

        False only in generating-first: nothing has played yet and the pool
        holds no member. Once a track is playing -- even a custom-named one
        whose stem does not match the (vibe, style) prefix -- or a pool member
        exists, advancing either rotates to another track or loops the current
        one, so the guard keys off the playing track, not the session prefix.
        """
        return self._track is not None or not self.is_empty

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

        When no pool member exists yet but a track is playing -- a named-first
        track whose stem does not match the pool prefix, with the fill not yet
        landed a member -- this loops the current track rather than raising,
        keeping the loop alive until a member lands. The empty-pool-with-nothing-
        playing case stays a tripwire (``ValueError``): the loop only advances
        after spawning a track, so it never reaches here.
        """
        pool = self._pool()
        if len(pool) == 0:
            if self._track is not None:
                return self._track
            msg = "cannot advance: pool is empty and nothing is playing"
            raise ValueError(msg)
        return pool.pick_next(self._track)

    def mark_playing(self, track: Path) -> None:
        """Record ``track`` as the now-playing (avoid-repeat) track."""
        self._track = track

    def clear_current(self) -> None:
        """Forget the avoid-repeat key (a stopped track is not 'just played')."""
        self._track = None

    def _pool(self) -> TrackPool:
        """Return the on-disk pool for the current prefix."""
        return TrackPool.from_paths(self._generator.tracks_for(self._pool_prefix))
