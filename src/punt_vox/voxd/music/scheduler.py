"""Music scheduling -- background loop that generates and loops music tracks."""
# pyright: reportPrivateUsage=false
# Internal module within the voxd package -- cross-module private access is expected.

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Self, cast

from punt_vox.voxd.music.generator import TrackGenerator
from punt_vox.voxd.music.playback_cmd import music_player_command
from punt_vox.voxd.music.types import MusicResponse

__all__ = [
    "_MUSIC_MAX_RETRIES",
    "MusicScheduler",
    "_PlaybackWaitResult",
]

logger = logging.getLogger(__name__)

_MUSIC_MAX_RETRIES = 3


@dataclass(frozen=True, slots=True)
class _PlaybackWaitResult:
    """Outcome of one iteration of the inner playback-wait loop.

    Returned by :meth:`MusicScheduler._playback_wait_loop` to tell
    :meth:`MusicScheduler.loop` what state transitions occurred while
    waiting on a subprocess.
    """

    current_track: Path | None
    gen_task: asyncio.Task[Path] | None
    retry_count: int
    handoff_occurred: bool


class MusicScheduler:
    """Background music loop: generate tracks, loop playback, handle vibe changes."""

    __slots__ = (
        "_changed",
        "_generator",
        "_loop_task",
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
    _loop_task: asyncio.Task[None] | None
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
        self._loop_task = None
        return self

    # -- Properties (writable, for existing handler delegation) ----------------
    # NOTE: Writable setters remain for now -- existing handlers in
    # music_handlers.py still use them. They will be removed in Step 5
    # when handlers are rewritten to use domain methods.

    @property
    def mode(self) -> str:
        """Return the current music mode ('off' or 'on')."""
        return self._mode

    @mode.setter
    def mode(self, value: str) -> None:
        self._mode = value

    @property
    def style(self) -> str:
        """Return the current music style."""
        return self._style

    @style.setter
    def style(self, value: str) -> None:
        self._style = value

    @property
    def owner(self) -> str:
        """Return the current music owner session ID."""
        return self._owner

    @owner.setter
    def owner(self, value: str) -> None:
        self._owner = value

    @property
    def vibe(self) -> tuple[str, str]:
        """Return the current (vibe_text, vibe_tags) tuple."""
        return self._vibe

    @vibe.setter
    def vibe(self, value: tuple[str, str]) -> None:
        self._vibe = value

    @property
    def track(self) -> Path | None:
        """Return the current track path."""
        return self._track

    @track.setter
    def track(self, value: Path | None) -> None:
        self._track = value

    @property
    def track_name(self) -> str:
        """Return the current track name."""
        return self._track_name

    @track_name.setter
    def track_name(self, value: str) -> None:
        self._track_name = value

    @property
    def proc(self) -> asyncio.subprocess.Process | None:
        """Return the current music subprocess."""
        return self._proc

    @proc.setter
    def proc(self, value: asyncio.subprocess.Process | None) -> None:
        self._proc = value

    @property
    def state(self) -> str:
        """Return the current music state ('idle', 'generating', 'playing')."""
        return self._state

    @state.setter
    def state(self, value: str) -> None:
        self._state = value

    @property
    def changed(self) -> asyncio.Event:
        """Return the music-changed event."""
        return self._changed

    @changed.setter
    def changed(self, value: asyncio.Event) -> None:
        self._changed = value

    @property
    def replay(self) -> bool:
        """Return whether replay mode is active."""
        return self._replay

    @replay.setter
    def replay(self, value: bool) -> None:
        self._replay = value

    @property
    def loop_task(self) -> asyncio.Task[None] | None:
        """Return the background loop task."""
        return self._loop_task

    @loop_task.setter
    def loop_task(self, value: asyncio.Task[None] | None) -> None:
        self._loop_task = value

    # -- Domain methods --------------------------------------------------------

    async def turn_on(
        self,
        owner_id: str,
        style: str,
        vibe: tuple[str, str],
        name: str,
    ) -> MusicResponse:
        """Start music or transfer ownership."""
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
        """Kill the current music subprocess if running.

        Temporary public wrapper around _kill_proc -- still called from
        daemon.py lifespan and existing handlers. Will be removed when
        handlers are rewritten in Step 5.
        """
        await self._kill_proc()

    async def loop(self) -> None:
        """Background task: generate and loop music tracks.

        Runs for the lifetime of the daemon.  When ``mode`` is "on",
        derives a prompt from the current vibe, generates a track via
        :class:`~punt_vox.providers.elevenlabs_music.ElevenLabsMusicProvider`,
        and loops it via ffplay at reduced volume.

        The key invariant is **gapless handoff**: the old track keeps
        looping in its own playback subprocess while generation runs as a
        concurrent ``asyncio.Task``.  The old subprocess is killed only
        once the new track is ready.  If the vibe changes again during
        generation, the in-flight generation task is cancelled and a fresh
        one starts -- the old track keeps looping throughout.

        Crash recovery: generation failures during playback are handled
        inline -- the old track keeps looping while the loop retries with
        exponential backoff (up to 3 attempts).  After 3 consecutive
        failures, ``mode`` is set to "off" and the old track is
        killed.  Initial generation failures (no old track yet) propagate
        to the outer handler which retries the entire cycle.
        """
        retry_count = 0
        current_track: Path | None = None
        gen_task: asyncio.Task[Path] | None = None

        while True:
            # Wait until music is turned on.
            while self._mode != "on":
                self._changed.clear()
                # Re-check after clear to avoid lost wakeup: a handler may
                # have set mode between our check and the clear().
                if self._mode == "on":
                    break
                await self._changed.wait()

            try:
                # --- Initial generation (no old track to loop) ----------------
                if current_track is None:
                    current_track = await self._run_initial_generation()
                    retry_count = 0
                    if current_track is None:
                        # Vibe changed during generation -- restart cycle.
                        continue

                # --- Playback loop: loop current_track, generate in parallel --
                # current_track is guaranteed non-None by the initial generation
                # block above -- the only path that sets it to None also continues
                # back to the top of the while loop.
                gen_task = None
                while self._mode == "on":
                    self._state = "playing" if gen_task is None else "generating"
                    cmd = music_player_command(current_track)
                    proc = await asyncio.create_subprocess_exec(
                        *cmd,
                        stdin=asyncio.subprocess.DEVNULL,
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                        start_new_session=True,
                    )
                    self._proc = proc

                    result = await self._playback_wait_loop(
                        proc,
                        current_track,
                        gen_task,
                        retry_count,
                    )
                    gen_task = result.gen_task
                    retry_count = result.retry_count

                    # Music was turned off or max retries exceeded.
                    if result.current_track is None:
                        current_track = None
                        break

                    # Handoff, replay, or natural subprocess end.
                    current_track = result.current_track

                    # After handoff, the old proc was killed intentionally --
                    # non-zero rc is expected, not worth warning about.
                    if not result.handoff_occurred:
                        rc = proc.returncode
                        if rc is not None and rc != 0:
                            logger.warning(
                                "Music playback ended with rc=%s for %s",
                                rc,
                                current_track.name,
                            )

            except asyncio.CancelledError:
                if gen_task is not None and not gen_task.done():
                    gen_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await gen_task
                    gen_task = None
                await self._kill_proc()
                self._state = "idle"
                current_track = None
                raise
            except Exception:
                logger.exception(
                    "Music loop error (attempt %d/%d)",
                    retry_count + 1,
                    _MUSIC_MAX_RETRIES,
                )
                if gen_task is not None and not gen_task.done():
                    gen_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await gen_task
                    gen_task = None
                await self._kill_proc()
                self._state = "idle"
                current_track = None
                retry_count += 1
                if retry_count >= _MUSIC_MAX_RETRIES:
                    logger.error(
                        "Music loop failed %d times, disabling music",
                        _MUSIC_MAX_RETRIES,
                    )
                    self._mode = "off"
                    retry_count = 0
                else:
                    # Exponential backoff: 1s, 2s, 4s...
                    await self._backoff_sleep(2 ** (retry_count - 1))

    # -- Private helpers -------------------------------------------------------

    async def _run_initial_generation(self) -> Path | None:
        """Handle first track when no old track exists.

        If replay is set, use the pre-placed track.  Otherwise generate
        a fresh track.  Returns None if a vibe change occurred during
        generation (caller should restart the cycle).
        """
        if self._replay:
            self._replay = False
            self._changed.clear()
            if self._track is None:
                msg = "music_replay set but music_track is None"
                raise RuntimeError(msg)
            return self._track

        self._state = "generating"
        self._changed.clear()
        track = await self._generate_track()
        self._track = track

        # Vibe changed during initial generation -- caller should
        # restart the cycle (no old track to keep looping).
        if self._changed.is_set():
            logger.info(
                "Vibe changed during initial generation, regenerating",
            )
            return None
        return track

    async def _handle_generation_complete(
        self,
        gen_task: asyncio.Task[Path],
        retry_count: int,
    ) -> tuple[Path | None, asyncio.Task[Path] | None, int]:
        """Handle a completed generation task (success or failure).

        Returns ``(new_track_or_none, new_gen_task_or_none, new_retry_count)``.
        On success: kills the old proc, returns the new track.
        On failure: retries with backoff, or disables music after max retries.
        """
        exc: BaseException | None = gen_task.exception()
        if exc is None:
            new_track: Path = gen_task.result()
            self._track = new_track
            await self._kill_proc()
            return new_track, None, 0

        # Generation failed -- old track keeps looping.
        retry_count += 1
        logger.error(
            "Generation failed during playback (attempt %d/%d), old track continues",
            retry_count,
            _MUSIC_MAX_RETRIES,
            exc_info=exc,
        )
        if retry_count >= _MUSIC_MAX_RETRIES:
            logger.error(
                "Music generation failed %d times, disabling music",
                _MUSIC_MAX_RETRIES,
            )
            self._mode = "off"
            await self._kill_proc()
            self._state = "idle"
            return None, None, 0

        # Under max retries: start a new gen_task after backoff.
        await self._backoff_sleep(2 ** (retry_count - 1))
        new_gen_task = asyncio.create_task(self._generate_track())
        self._state = "generating"
        return None, new_gen_task, retry_count

    async def _handle_vibe_change(
        self,
        gen_task: asyncio.Task[Path] | None,
    ) -> tuple[asyncio.Task[Path] | None, Path | None]:
        """Handle a vibe-changed event during playback.

        Cancels any in-flight generation, handles replay, or starts new
        generation.  Returns ``(new_gen_task_or_none, replay_track_or_none)``.
        """
        self._changed.clear()
        if gen_task is not None and not gen_task.done():
            gen_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await gen_task

        # Replay: handler pre-set self._track.
        if self._replay:
            self._replay = False
            if self._track is None:
                msg = "music_replay set but music_track is None"
                raise RuntimeError(msg)
            replay_track: Path = self._track
            await self._kill_proc()
            return None, replay_track

        self._state = "generating"
        new_gen_task = asyncio.create_task(self._generate_track())
        return new_gen_task, None

    async def _kill_proc(self) -> None:
        """Kill the current music subprocess if running."""
        proc = self._proc
        if proc is not None and proc.returncode is None:
            proc.kill()
            with contextlib.suppress(Exception):
                await proc.wait()
        self._proc = None

    async def _generate_track(self) -> Path:
        """Generate a music track from the current vibe and style.

        Thin wrapper around TrackGenerator.generate that reads from and
        writes back to scheduler fields.
        """
        track_path, resolved_name = await self._generator.generate(
            self._vibe, self._style, self._track_name
        )
        self._track_name = resolved_name
        return track_path

    async def _playback_wait_loop(
        self,
        proc: asyncio.subprocess.Process,
        current_track: Path,
        gen_task: asyncio.Task[Path] | None,
        retry_count: int,
    ) -> _PlaybackWaitResult:
        """Wait on a music subprocess, handling events until it should stop.

        Races the subprocess against ``self._changed`` and an optional
        in-flight generation task.  Handles music-off, generation completion
        (success and failure with retry), vibe changes, replay, and natural
        subprocess termination.

        Returns a :class:`_PlaybackWaitResult` describing the new state.
        The caller uses this to decide whether to respawn the subprocess,
        break out of the playback loop, or continue with a new track.
        """

        while True:
            wait_task = asyncio.create_task(proc.wait())
            changed_task: asyncio.Task[bool] = asyncio.create_task(
                self._changed.wait(),
            )
            waitables: set[asyncio.Future[object]] = {
                cast("asyncio.Future[object]", wait_task),
                cast("asyncio.Future[object]", changed_task),
            }
            if gen_task is not None:
                waitables.add(cast("asyncio.Future[object]", gen_task))

            _done, pending = await asyncio.wait(
                waitables,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in pending:
                # Don't cancel the generation task -- it may
                # still be running and we want it to finish.
                if t is gen_task:
                    continue
                t.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await t

            # --- /music off: kill everything immediately ----------
            if self._mode != "on":
                if gen_task is not None and not gen_task.done():
                    gen_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await gen_task
                    gen_task = None
                await self._kill_proc()
                return _PlaybackWaitResult(
                    current_track=None,
                    gen_task=None,
                    retry_count=retry_count,
                    handoff_occurred=False,
                )

            # --- Generation task completed: handoff ---------------
            if gen_task is not None and gen_task.done():
                (
                    new_track,
                    new_gen,
                    retry_count,
                ) = await self._handle_generation_complete(gen_task, retry_count)
                gen_task = new_gen
                if new_track is not None:
                    # Successful handoff.
                    return _PlaybackWaitResult(
                        current_track=new_track,
                        gen_task=None,
                        retry_count=retry_count,
                        handoff_occurred=True,
                    )
                if gen_task is None:
                    # Max retries exceeded -- music disabled.
                    return _PlaybackWaitResult(
                        current_track=None,
                        gen_task=None,
                        retry_count=retry_count,
                        handoff_occurred=False,
                    )
                # Retry in progress -- re-enter wait loop.
                continue

            # --- Vibe changed: start/restart generation -----------
            if self._changed.is_set():
                new_gen, replay_track = await self._handle_vibe_change(gen_task)
                gen_task = new_gen
                if replay_track is not None:
                    return _PlaybackWaitResult(
                        current_track=replay_track,
                        gen_task=None,
                        retry_count=0,
                        handoff_occurred=False,
                    )
                # New generation started -- re-enter wait loop.
                continue

            # --- Subprocess ended naturally -----------------------
            # Return the same current_track so the caller respawns it.
            return _PlaybackWaitResult(
                current_track=current_track,
                gen_task=gen_task,
                retry_count=retry_count,
                handoff_occurred=False,
            )

    async def _backoff_sleep(self, seconds: float) -> None:
        """Sleep for backoff in the music loop, interruptible by changed.

        Returns immediately if ``changed`` fires or ``mode``
        becomes ``"off"`` during the wait.  This lets ``/music off`` and
        vibe changes break out of exponential backoff without blocking
        for the full sleep duration.
        """
        sleep_task = asyncio.create_task(asyncio.sleep(seconds))
        changed_task = asyncio.create_task(self._changed.wait())
        _done, pending = await asyncio.wait(
            {sleep_task, changed_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await t
