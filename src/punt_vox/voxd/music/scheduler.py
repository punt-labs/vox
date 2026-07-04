"""Music scheduling -- session state, domain commands, and the control channel.

The scheduler owns the session state (mode, owner, playback subprocess), the
control channel the loop reads, and the handler-facing domain commands. It
delegates the pool -- its background fill and the next-track selection -- to a
:class:`~punt_vox.voxd.music.playlist.Playlist`. It never plays audio: the loop
(:class:`~punt_vox.voxd.music.loop.MusicLoop`) owns the subprocess and the
advance-on-track-end wiring.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Self

from punt_vox.voxd.music.generator import TrackGenerator
from punt_vox.voxd.music.playlist import Playlist
from punt_vox.voxd.music.types import MusicResponse

__all__ = ["MusicRequest", "MusicScheduler"]

_NAME_MAX_LEN = 60

# The pending playback action the loop reads at the next control point.
MusicControl = Literal["none", "off", "skip", "play", "vibe"]


@dataclass(frozen=True, slots=True)
class MusicRequest:
    """A request to start or change music for one session."""

    owner_id: str
    style: str
    vibe: tuple[str, str]
    name: str


class MusicScheduler:
    """Session state, domain commands, and the loop control channel."""

    __slots__ = (
        "_changed",
        "_control",
        "_mode",
        "_owner",
        "_pending_track",
        "_playlist",
        "_proc",
        "_state",
    )

    _changed: asyncio.Event
    _control: MusicControl
    _mode: str
    _owner: str
    _pending_track: Path | None
    _playlist: Playlist
    _proc: asyncio.subprocess.Process | None
    _state: str

    def __new__(cls, track_generator: TrackGenerator) -> Self:
        self = super().__new__(cls)
        self._playlist = Playlist(track_generator)
        self._mode = "off"
        self._owner = ""
        self._pending_track = None
        self._proc = None
        self._state = "idle"
        self._changed = asyncio.Event()
        self._control = "none"
        return self

    @property
    def mode(self) -> str:
        """Return the current music mode ('off' or 'on')."""
        return self._mode

    @property
    def style(self) -> str:
        """Return the current music style."""
        return self._playlist.style

    @property
    def owner(self) -> str:
        """Return the current music owner session ID."""
        return self._owner

    @property
    def vibe(self) -> tuple[str, str]:
        """Return the current (vibe_text, vibe_tags) tuple."""
        return self._playlist.vibe

    @property
    def track(self) -> Path | None:
        """Return the current (playing / just-played) track path."""
        return self._playlist.current_track

    @property
    def proc(self) -> asyncio.subprocess.Process | None:
        """Return the current music subprocess."""
        return self._proc

    @property
    def state(self) -> str:
        """Return the current music state ('idle', 'generating', 'playing')."""
        return self._state

    @property
    def changed(self) -> asyncio.Event:
        """Return the control-signal event the loop races against playback."""
        return self._changed

    @property
    def filling(self) -> bool:
        """Return whether a background fill task is currently live."""
        return self._playlist.filling

    @property
    def has_pending_track(self) -> bool:
        """Return whether a named track is queued to play (a /music play)."""
        return self._pending_track is not None

    async def turn_on(
        self,
        owner_id: str,
        style: str,
        vibe: tuple[str, str],
        name: str,
    ) -> MusicResponse:
        """Start music or transfer ownership for one (vibe, style) pool.

        A ``name`` matching a saved track replays it. Otherwise the pool is
        adopted and the background fill (re)starts from the on-disk count;
        an empty pool generates the first track (named when ``name`` is set),
        a non-empty pool plays immediately.
        """
        if not owner_id:
            msg = "owner_id is required"
            raise ValueError(msg)
        req = MusicRequest(owner_id, style, vibe, name)
        if name:
            replayed = await self._replay_named(req)
            if replayed is not None:
                return replayed
        await self._adopt(req)
        first_name = TrackGenerator.slugify(name, _NAME_MAX_LEN) if name else ""
        self._playlist.ensure_fill(first_name=first_name)
        self._signal("play")
        return self._enter_ack()

    async def turn_off(self) -> MusicResponse:
        """Stop playback and cancel the fill synchronously (no orphaned gen)."""
        self._playlist.cancel_fill()
        await self._kill_proc()
        self._mode = "off"
        self._state = "idle"
        self._pending_track = None
        # The avoid-repeat key must clear (PY-EN-5): a stopped track is not
        # "just played", so the next rotation should not exclude it.
        self._playlist.clear_current()
        self._signal("off")
        return MusicResponse(status="stopped")

    async def play_track(self, name: str, owner_id: str) -> MusicResponse:
        """Replay a saved track by name, switching to its pool (no fill)."""
        if not name:
            msg = "name is required"
            raise ValueError(msg)
        if not owner_id:
            msg = "owner_id is required"
            raise ValueError(msg)
        safe_name = TrackGenerator.slugify(name, _NAME_MAX_LEN)
        if not safe_name:
            msg = "invalid track name"
            raise ValueError(msg)
        track_path = self._playlist.find(name)
        if track_path is None:
            msg = f"track not found: {safe_name}"
            raise ValueError(msg)

        await self._kill_proc()
        self._playlist.cancel_fill()  # leaving the filling pool -- no named fill
        self._mode = "on"
        self._owner = owner_id
        self._playlist.set_prefix(TrackGenerator.pool_prefix_of(track_path))
        return self._queue_named(track_path, safe_name)

    def update_vibe(self, owner_id: str, vibe: tuple[str, str]) -> MusicResponse:
        """Update the vibe if the sender owns music; retarget fill immediately.

        The playback switch is deferred to the current song's natural end
        (finish-current-first); the fill retargets now so the new pool is
        ready, bounding credit spend to a single pool.
        """
        if not owner_id:
            msg = "owner_id is required"
            raise ValueError(msg)
        if owner_id != self._owner or vibe == self._playlist.vibe:
            return MusicResponse(status="ignored")
        self._playlist.retune(vibe, self._playlist.style)
        self._playlist.ensure_fill()
        self._signal("vibe")
        return self._enter_ack()

    def skip_next(self, owner_id: str) -> MusicResponse:
        """Advance to the next track. A no-op while the pool is still empty."""
        if not owner_id:
            msg = "owner_id is required"
            raise ValueError(msg)
        if self._mode != "on" or self._playlist.is_empty:
            # Z finding #1: skip in generating-first must not pick from an
            # empty pool -- there is nothing to advance to until #1 lands.
            return MusicResponse(status="ignored")
        self._signal("skip")
        return MusicResponse(status="playing")

    async def wait_active(self) -> None:
        """Block until music is turned on."""
        while self._mode != "on":
            await self._changed.wait()
            self._changed.clear()

    def take_control(self) -> MusicControl:
        """Return the pending control action and reset it to 'none'."""
        control = self._control
        self._control = "none"
        return control

    def ensure_fill(self) -> None:
        """(Re)start the background fill for the current pool if not full."""
        self._playlist.ensure_fill()

    @property
    def pool_empty(self) -> bool:
        """Return whether the current pool has no tracks on disk yet."""
        return self._playlist.is_empty

    def select_first(self) -> Path:
        """Return an initial track to play from a non-empty pool."""
        return self._playlist.select_first()

    def mark_generating(self) -> None:
        """Record that the pool is empty and the first track is being made."""
        self._state = "generating"

    async def await_first_track(self) -> Path:
        """Wait for the fill to deliver the first track of an empty pool."""
        return await self._playlist.await_first_track()

    def select_next_track(self) -> Path:
        """Return the next track to play, avoiding the just-played one."""
        return self._playlist.select_next()

    def take_pending_track(self) -> Path:
        """Return the queued named track (a /music play) and clear it."""
        track = self._pending_track
        self._pending_track = None
        if track is None:
            msg = "take_pending_track called with no track queued"
            raise RuntimeError(msg)
        return track

    def mark_playing(self, track: Path) -> None:
        """Record ``track`` as the now-playing track and enter the playing state."""
        self._playlist.mark_playing(track)
        self._state = "playing"

    def begin_playback(self, proc: asyncio.subprocess.Process) -> None:
        """Record the player subprocess the loop just spawned."""
        self._proc = proc

    async def kill_proc(self) -> None:
        """Kill the current music subprocess if running."""
        await self._kill_proc()

    def disable(self) -> None:
        """Turn music off after an unrecoverable failure (no track to play)."""
        self._playlist.cancel_fill()
        self._mode = "off"
        self._state = "idle"

    async def shutdown(self) -> None:
        """Daemon lifespan cleanup: cancel fill, kill proc, reset state."""
        self._playlist.cancel_fill()
        await self._kill_proc()
        self._state = "idle"

    async def _replay_named(self, req: MusicRequest) -> MusicResponse | None:
        """Replay a saved track by name, or None if it must be generated."""
        safe_name = TrackGenerator.slugify(req.name, _NAME_MAX_LEN)
        track_path = self._playlist.find(req.name)
        if track_path is None:
            if not safe_name:
                msg = "invalid track name"
                raise ValueError(msg)
            return None
        await self._adopt(req)
        self._playlist.cancel_fill()
        self._playlist.set_prefix(TrackGenerator.pool_prefix_of(track_path))
        return self._queue_named(track_path, safe_name)

    async def _adopt(self, req: MusicRequest) -> None:
        """Kill any foreign playback and adopt ownership for a new pool."""
        is_already_playing = self._mode == "on" and self._proc is not None
        if not is_already_playing or self._owner != req.owner_id:
            await self._kill_proc()
        self._mode = "on"
        self._owner = req.owner_id
        self._playlist.retune(req.vibe, req.style)

    def _queue_named(self, track: Path, name: str) -> MusicResponse:
        """Queue ``track`` as the next thing to play (a named replay)."""
        self._pending_track = track
        self._state = "playing"
        self._signal("play")
        return MusicResponse(status="playing", track=str(track), name=name)

    def _enter_ack(self) -> MusicResponse:
        """Return the ack status for entering the current pool."""
        if self._playlist.is_empty:
            return MusicResponse(status="generating")
        return MusicResponse(status="playing")

    def _signal(self, control: MusicControl) -> None:
        """Record a control action and wake the loop."""
        self._control = control
        self._changed.set()

    async def _kill_proc(self) -> None:
        """Kill the current music subprocess if running."""
        proc = self._proc
        if proc is not None and proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            with contextlib.suppress(ProcessLookupError, OSError):
                await proc.wait()
        self._proc = None
