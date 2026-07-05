"""Music scheduling -- session state and the handler-facing domain commands.

Ownership and the loop control signal live in a
:class:`~punt_vox.voxd.music.control.MusicControlChannel`; the pool (background
fill and next-track selection) lives in a
:class:`~punt_vox.voxd.music.playlist.Playlist`. The scheduler never plays audio
-- the loop (:class:`~punt_vox.voxd.music.loop.MusicLoop`) owns the subprocess.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Self

from punt_vox.voxd.music.control import MusicControl, MusicControlChannel
from punt_vox.voxd.music.generator import TrackGenerator
from punt_vox.voxd.music.playlist import Playlist
from punt_vox.voxd.music.types import MusicResponse

if TYPE_CHECKING:
    from punt_vox.music_prompts import PromptSet

__all__ = ["MusicRequest", "MusicScheduler"]

_NAME_MAX_LEN = 60

_NO_KEY_MSG = "Background music requires an ElevenLabs API key (set ELEVENLABS_API_KEY)"


@dataclass(frozen=True, slots=True)
class MusicRequest:
    """A request to start or change music for one session.

    ``prompts`` is the agent-authored generation prompts, or ``None`` to fall
    back to a minimal literal prompt.
    """

    owner_id: str
    style: str
    vibe: tuple[str, str]
    name: str
    prompts: PromptSet | None = None


class MusicScheduler:
    """Session state and the handler-facing domain commands."""

    __slots__ = (
        "_channel",
        "_pending_track",
        "_playlist",
        "_proc",
        "_state",
    )

    _channel: MusicControlChannel
    _pending_track: Path | None
    _playlist: Playlist
    _proc: asyncio.subprocess.Process | None
    _state: str

    def __new__(cls, track_generator: TrackGenerator) -> Self:
        self = super().__new__(cls)
        self._playlist = Playlist(track_generator)
        self._channel = MusicControlChannel()
        self._pending_track = None
        self._proc = None
        self._state = "idle"
        return self

    @property
    def mode(self) -> str:
        """Return the current music mode ('off' or 'on')."""
        return "on" if self._channel.active else "off"

    @property
    def style(self) -> str:
        """Return the current music style."""
        return self._playlist.style

    @property
    def owner(self) -> str:
        """Return the current music owner session ID."""
        return self._channel.owner

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
        return self._channel.changed

    @property
    def filling(self) -> bool:
        """Return whether a background fill task is currently live."""
        return self._playlist.filling

    @property
    def has_pending_track(self) -> bool:
        """Return whether a named track is queued to play (a /music play)."""
        return self._pending_track is not None

    async def turn_on(self, req: MusicRequest) -> MusicResponse:
        """Start music or transfer ownership for one (vibe, style) pool.

        A ``req.name`` matching a saved track replays it; otherwise the pool is
        adopted, the agent's ``req.prompts`` (or the fallback when ``None``) are
        installed, and an empty pool generates its first track before playing.
        """
        if not req.owner_id:
            msg = "owner_id is required"
            raise ValueError(msg)
        if req.name:
            replayed = await self._replay_named(req)
            if replayed is not None:
                return replayed
        # Preflight the key: a missing key fails fast, not a silent self-disable.
        if not self._playlist.can_generate():
            raise ValueError(_NO_KEY_MSG)
        await self._adopt(req)
        self._playlist.set_prompts(req.prompts)
        first = TrackGenerator.slugify(req.name, _NAME_MAX_LEN) if req.name else ""
        self._playlist.ensure_fill(first_name=first)
        # A retarget queues no track: signal "vibe", not "play" (which is paired
        # with a queued track in _queue_named, never taken from an empty queue).
        self._channel.signal("vibe")
        return self._enter_ack()

    async def turn_off(self) -> MusicResponse:
        """Stop playback and cancel the fill synchronously (no orphaned gen)."""
        self._playlist.cancel_fill()
        await self.kill_proc()
        self._reset_session()
        self._channel.signal("off")
        return MusicResponse(status="stopped")

    async def play_track(self, name: str, owner_id: str) -> MusicResponse:
        """Replay a saved track by name, switching to its pool and resuming fill."""
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

        await self.kill_proc()
        self._channel.activate()
        self._channel.claim(owner_id)
        return self._switch_to_replayed_pool(track_path, safe_name, prompts=None)

    def update_vibe(self, owner_id: str, vibe: tuple[str, str]) -> MusicResponse:
        """Update the vibe if the owner drives *playing* music; retarget fill now.

        The playback switch defers to the current song's natural end; the fill
        retargets now so the new pool is ready. ``mode == "on"`` gates it -- a
        forwarded vibe while music is off must not spend credits (finding #4).
        """
        if not owner_id:
            msg = "owner_id is required"
            raise ValueError(msg)
        if (
            not self._channel.active
            or not self._channel.owned_by(owner_id)
            or vibe == self._playlist.vibe
        ):
            return MusicResponse(status="ignored")
        self._playlist.retune(vibe, self._playlist.style)
        self._playlist.ensure_fill()
        self._channel.signal("vibe")
        return self._enter_ack()

    def skip_next(self, owner_id: str) -> MusicResponse:
        """Advance to the next track. A no-op only in generating-first."""
        if not owner_id:
            msg = "owner_id is required"
            raise ValueError(msg)
        if not self._channel.active or not self._playlist.can_advance:
            # No-op only in generating-first (Z finding #1). ``can_advance`` keys
            # off the playing track, not the (vibe, style) glob, so a custom-named
            # track still advances even though its stem misses the glob (finding #2).
            return MusicResponse(status="ignored")
        self._channel.signal("skip")
        return MusicResponse(status="playing")

    async def wait_active(self) -> None:
        """Block until music is turned on."""
        await self._channel.wait_active()

    def take_control(self) -> MusicControl:
        """Return the pending control action and reset it to 'none'."""
        return self._channel.take()

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

    def disable(self) -> None:
        """Turn music off after an unrecoverable failure (no track to play)."""
        self._playlist.cancel_fill()
        self._reset_session()

    def _reset_session(self) -> None:
        """Return to the idle, unowned state, leaving nothing to act on later.

        Shared by ``turn_off`` and ``disable``: clears the mode/state machine,
        releases ownership (so a stale forwarded vibe is not accepted), drops any
        queued replay, and clears the avoid-repeat key (PY-EN-5) -- a stopped
        track is not "just played" and must not be excluded from the next pool.
        """
        self._channel.deactivate()
        self._state = "idle"
        self._pending_track = None
        self._channel.release()
        self._playlist.clear_current()

    async def shutdown(self) -> None:
        """Daemon lifespan cleanup: cancel fill, kill proc, reset state."""
        self._playlist.cancel_fill()
        await self.kill_proc()
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
        return self._switch_to_replayed_pool(track_path, safe_name, req.prompts)

    async def _adopt(self, req: MusicRequest) -> None:
        """Kill any foreign playback and adopt ownership for a new pool."""
        is_already_playing = self._channel.active and self._proc is not None
        if not is_already_playing or not self._channel.owned_by(req.owner_id):
            await self.kill_proc()
        self._channel.activate()
        self._channel.claim(req.owner_id)
        self._playlist.retune(req.vibe, req.style)

    def _switch_to_replayed_pool(
        self, track: Path, name: str, prompts: PromptSet | None
    ) -> MusicResponse:
        """Retarget selection and fill to a replayed track's pool, then queue it.

        Named replay retargets both selection and fill to the track's pool
        (DES-039): the old fill is cancelled and a fresh one starts (a no-op on a
        full pool), so the replayed pool keeps growing toward POOL_SIZE. Fill and
        selection share the pool prefix, so credits go to the pool being played.

        ``prompts`` is the agent-authored :class:`PromptSet` for the replayed pool
        (an agent-driven ``/music on --name X``) or ``None`` for a plain replay
        (``/music play``). It is installed after ``set_prefix`` clears the stale
        set, so the fill uses the authored base + variations, not the fallback.
        """
        self._playlist.cancel_fill()
        self._playlist.set_prefix(TrackGenerator.pool_prefix_of(track))
        self._playlist.set_prompts(prompts)
        self._playlist.ensure_fill()
        return self._queue_named(track, name)

    def _queue_named(self, track: Path, name: str) -> MusicResponse:
        """Queue ``track`` as the next thing to play (a named replay)."""
        self._pending_track = track
        self._state = "playing"
        self._channel.signal("play")
        return MusicResponse(status="playing", track=str(track), name=name)

    def _enter_ack(self) -> MusicResponse:
        """Return the ack status for entering the current pool."""
        if self._playlist.is_empty:
            return MusicResponse(status="generating")
        return MusicResponse(status="playing")

    async def kill_proc(self) -> None:
        """Kill the current music subprocess if running."""
        proc = self._proc
        if proc is not None and proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            with contextlib.suppress(ProcessLookupError, OSError):
                await proc.wait()
        self._proc = None
