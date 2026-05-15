"""Music scheduling -- domain operations and state ownership."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path
from typing import Self

from punt_vox.voxd.music.generator import TrackGenerator
from punt_vox.voxd.music.types import MusicResponse

__all__ = [
    "MusicScheduler",
]

logger = logging.getLogger(__name__)


class MusicScheduler:
    """State ownership and domain methods for the music subsystem."""

    __slots__ = (
        "_changed",
        "_generator",
        "_mode",
        "_owner",
        "_proc",
        "_replay",
        "_state",
        "_style",
        "_track",
        "_track_name",
        "_vibe",
    )

    _changed: asyncio.Event
    _generator: TrackGenerator
    _mode: str
    _owner: str
    _proc: asyncio.subprocess.Process | None
    _replay: bool
    _state: str
    _style: str
    _track: Path | None
    _track_name: str
    _vibe: tuple[str, str]

    def __new__(cls, track_generator: TrackGenerator) -> Self:
        self = super().__new__(cls)
        self._generator = track_generator
        self._mode = "off"
        self._style = ""
        self._owner = ""
        self._vibe = ("", "")
        self._track = None
        self._track_name = ""
        self._proc = None
        self._state = "idle"
        self._changed = asyncio.Event()
        self._replay = False
        return self

    # -- Read-only properties for external inspection --------------------------

    @property
    def mode(self) -> str:
        """Return the current music mode ('off' or 'on')."""
        return self._mode

    @property
    def style(self) -> str:
        """Return the current music style."""
        return self._style

    @property
    def owner(self) -> str:
        """Return the current music owner session ID."""
        return self._owner

    @property
    def vibe(self) -> tuple[str, str]:
        """Return the current (vibe_text, vibe_tags) tuple."""
        return self._vibe

    @property
    def track(self) -> Path | None:
        """Return the current track path."""
        return self._track

    @property
    def track_name(self) -> str:
        """Return the current track name."""
        return self._track_name

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
        """Return the music-changed event."""
        return self._changed

    @property
    def replay(self) -> bool:
        """Return whether replay mode is active."""
        return self._replay

    # -- Domain methods --------------------------------------------------------

    async def turn_on(
        self,
        owner_id: str,
        style: str,
        vibe: tuple[str, str],
        name: str,
    ) -> MusicResponse:
        """Start music or transfer ownership.

        If ``name`` is provided and a matching track exists, replays it
        (status="playing"). If ``name`` is provided but no matching track
        exists, generates a new track with that name (status="generating").
        If ``name`` is empty, generates with an auto-derived name.
        """
        if not owner_id:
            msg = "owner_id is required"
            raise ValueError(msg)

        # If name provided, validate and check for existing track.
        if name:
            track_path = self._generator.find_track(name)
            if track_path is None and not TrackGenerator.slugify(name, max_len=60):
                msg = "invalid track name"
                raise ValueError(msg)
            if track_path is not None:
                # Replay existing track.
                await self._kill_proc()
                self._mode = "on"
                if style:
                    self._style = style
                self._owner = owner_id
                self._vibe = vibe
                self._track = track_path
                self._track_name = TrackGenerator.slugify(name, max_len=60)
                self._state = "playing"
                self._replay = True
                self._changed.set()
                return MusicResponse(
                    status="playing",
                    track=str(track_path),
                    name=self._track_name,
                )

        # New generation.
        is_already_playing = self._mode == "on" and self._proc is not None
        if not is_already_playing or self._owner != owner_id:
            await self._kill_proc()

        self._mode = "on"
        if style:
            self._style = style
        self._owner = owner_id
        self._vibe = vibe
        self._track_name = TrackGenerator.slugify(name, max_len=60) if name else ""
        self._replay = False
        self._state = "generating"
        self._changed.set()
        return MusicResponse(status="generating")

    async def turn_off(self) -> MusicResponse:
        """Stop music playback."""
        await self._kill_proc()
        self._mode = "off"
        self._state = "idle"
        self._replay = False
        self._changed.set()
        return MusicResponse(status="stopped")

    async def play_track(self, name: str, owner_id: str) -> MusicResponse:
        """Replay a saved track by name."""
        if not name:
            msg = "name is required"
            raise ValueError(msg)
        if not owner_id:
            msg = "owner_id is required"
            raise ValueError(msg)
        safe_name = TrackGenerator.slugify(name, max_len=60)
        if not safe_name:
            msg = "invalid track name"
            raise ValueError(msg)
        track_path = self._generator.find_track(name)
        if track_path is None:
            msg = f"track not found: {safe_name}"
            raise ValueError(msg)

        await self._kill_proc()
        self._mode = "on"
        self._owner = owner_id
        self._track = track_path
        self._track_name = safe_name
        self._state = "playing"
        self._replay = True
        self._changed.set()
        return MusicResponse(status="playing", track=str(track_path), name=safe_name)

    def update_vibe(self, owner_id: str, vibe: tuple[str, str]) -> MusicResponse:
        """Update vibe if sender is owner."""
        if not owner_id:
            msg = "owner_id is required"
            raise ValueError(msg)
        if owner_id != self._owner:
            return MusicResponse(status="ignored")
        if vibe == self._vibe:
            return MusicResponse(status="ignored")
        self._vibe = vibe
        self._changed.set()
        return MusicResponse(status="generating")

    def skip_next(self, owner_id: str) -> MusicResponse:
        """Skip to a new track."""
        if not owner_id:
            msg = "owner_id is required"
            raise ValueError(msg)
        if self._mode != "on":
            return MusicResponse(status="ignored")
        self._track_name = ""
        self._replay = False
        self._changed.set()
        return MusicResponse(status="generating")

    # -- Public methods --------------------------------------------------------

    async def kill_proc(self) -> None:
        """Kill the current music subprocess if running."""
        await self._kill_proc()

    # -- Intent methods (used by MusicLoop) ------------------------------------

    def begin_generation(self) -> None:
        """Loop is starting a generation pass."""
        self._state = "generating"

    def complete_generation(self, track: Path) -> None:
        """Loop finished generating; record the track."""
        self._track = track
        self._track_name = track.stem

    def begin_playback(self, proc: asyncio.subprocess.Process) -> None:
        """Loop started a playback subprocess."""
        self._proc = proc
        self._state = "playing"

    def disable(self) -> None:
        """Loop exhausted retries; disable music."""
        self._mode = "off"
        self._state = "idle"

    def consume_replay(self) -> Path:
        """Loop consuming a replay directive. Clears flag, returns track."""
        self._replay = False
        if self._track is None:
            msg = "music_replay set but music_track is None"
            raise RuntimeError(msg)
        return self._track

    async def shutdown(self) -> None:
        """Daemon lifespan cleanup. Kills proc, resets state."""
        await self._kill_proc()
        self._state = "idle"

    async def generate_track(self) -> Path:
        """Generate a music track from the current vibe and style.

        Facade over scheduler state -- the loop calls this without knowing
        about _vibe, _style, or _track_name.
        """
        track_path, resolved_name = await self._generator.generate(
            self._vibe, self._style, self._track_name
        )
        self._track_name = resolved_name
        return track_path

    # -- Private ---------------------------------------------------------------

    async def _kill_proc(self) -> None:
        """Kill the current music subprocess if running."""
        proc = self._proc
        if proc is not None and proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            with contextlib.suppress(ProcessLookupError, OSError):
                await proc.wait()
        self._proc = None
