"""Async playback coordination: event racing, gapless handoff, retry/backoff.

Exceeds 300-line module_size threshold (PY-OO-2). This is a documented
exception -- the async event-racing logic is one coherent responsibility.
Splitting further would create coupling without cohesion.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Self, cast

from punt_vox.voxd.music.playback_cmd import music_player_command

if TYPE_CHECKING:
    from punt_vox.voxd.music.scheduler import MusicScheduler

__all__ = [
    "MusicLoop",
]

logger = logging.getLogger(__name__)

_MUSIC_MAX_RETRIES = 3


@dataclass(frozen=True, slots=True)
class _PlaybackWaitResult:
    """Outcome of one iteration of the inner playback-wait loop.

    Returned by :meth:`MusicLoop._playback_wait_loop` to tell
    :meth:`MusicLoop.run` what state transitions occurred while
    waiting on a subprocess.
    """

    current_track: Path | None
    gen_task: asyncio.Task[Path] | None
    retry_count: int
    handoff_occurred: bool


class MusicLoop:
    """Async playback coordination: event racing, gapless handoff, retry/backoff.

    Exceeds 300-line module_size threshold (PY-OO-2). This is a documented
    exception -- the async event-racing logic is one coherent responsibility.
    Splitting further would create coupling without cohesion.
    """

    __slots__ = ("_scheduler",)
    _scheduler: MusicScheduler

    def __new__(cls, scheduler: MusicScheduler) -> Self:
        self = super().__new__(cls)
        self._scheduler = scheduler
        return self

    async def run(self) -> None:
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
        sched = self._scheduler

        while True:
            await self._wait_for_mode_on()

            try:
                # --- Initial generation (no old track to loop) ----------------
                if current_track is None:
                    current_track = await self._run_initial_generation()
                    retry_count = 0
                    if current_track is None:
                        continue

                # --- Playback loop: loop current_track, generate in parallel --
                gen_task = None
                while sched.mode == "on":
                    (
                        current_track,
                        gen_task,
                        retry_count,
                    ) = await self._run_playback_iteration(
                        current_track, gen_task, retry_count
                    )
                    if current_track is None:
                        break

            except asyncio.CancelledError:
                await self._cancel_gen_task(gen_task)
                gen_task = None
                await sched.kill_proc()
                current_track = None
                raise
            except Exception:
                logger.exception(
                    "Music loop error (attempt %d/%d)",
                    retry_count + 1,
                    _MUSIC_MAX_RETRIES,
                )
                await self._cancel_gen_task(gen_task)
                gen_task = None
                # mode stays "on" intentionally during retry -- the loop will
                # attempt generation again after backoff. state="idle" is
                # correct: nothing is actively playing or generating during
                # the backoff window.
                await sched.shutdown()
                current_track = None
                retry_count += 1
                if retry_count >= _MUSIC_MAX_RETRIES:
                    logger.error(
                        "Music loop failed %d times, disabling music",
                        _MUSIC_MAX_RETRIES,
                    )
                    sched.disable()
                    retry_count = 0
                else:
                    await self._backoff_sleep(2 ** (retry_count - 1))

    # -- Private helpers -------------------------------------------------------

    async def _wait_for_mode_on(self) -> None:
        """Block until the scheduler's mode becomes 'on'."""
        sched = self._scheduler
        while sched.mode != "on":
            sched.changed.clear()
            if sched.mode == "on":
                break
            await sched.changed.wait()

    async def _run_playback_iteration(
        self,
        current_track: Path,
        gen_task: asyncio.Task[Path] | None,
        retry_count: int,
    ) -> tuple[Path | None, asyncio.Task[Path] | None, int]:
        """Run one iteration of the playback loop.

        Spawns the player subprocess, waits for events, and returns the
        updated (current_track, gen_task, retry_count). Returns
        current_track=None when the loop should break.
        """
        sched = self._scheduler
        cmd = music_player_command(current_track)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        sched.begin_playback(proc)
        if gen_task is not None:
            sched.begin_generation()

        result = await self._playback_wait_loop(
            proc, current_track, gen_task, retry_count
        )

        if result.current_track is None:
            return None, result.gen_task, result.retry_count

        if not result.handoff_occurred:
            await self._log_playback_exit(proc, result.current_track)

        return result.current_track, result.gen_task, result.retry_count

    @staticmethod
    async def _cancel_gen_task(
        gen_task: asyncio.Task[Path] | None,
    ) -> None:
        """Cancel an in-flight generation task if running."""
        if gen_task is not None and not gen_task.done():
            gen_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await gen_task

    @staticmethod
    async def _log_playback_exit(proc: asyncio.subprocess.Process, track: Path) -> None:
        """Log a warning if the playback subprocess exited with an error."""
        rc = proc.returncode
        if rc is not None and rc != 0:
            stderr_text = ""
            if proc.stderr is not None:
                stderr_bytes = await proc.stderr.read()
                stderr_text = stderr_bytes.decode(errors="replace").strip()
            logger.warning(
                "Music playback ended with rc=%s for %s: %s",
                rc,
                track.name,
                stderr_text,
            )

    async def _run_initial_generation(self) -> Path | None:
        """Handle first track when no old track exists.

        If replay is set, use the pre-placed track.  Otherwise generate
        a fresh track.  Returns None if a vibe change occurred during
        generation (caller should restart the cycle).
        """
        sched = self._scheduler
        if sched.replay:
            track = sched.consume_replay()
            sched.changed.clear()
            return track

        sched.begin_generation()
        sched.changed.clear()
        track = await sched.generate_track()
        sched.complete_generation(track)

        # A skip/vibe-change arrived mid-fill -- restart the cycle. Re-decide
        # first: the just-saved track may have filled the pool, so a now-full
        # pool rotates for free instead of paying for another generation.
        if sched.changed.is_set():
            logger.info("Signal during initial generation, re-deciding")
            sched.reconsider_next()
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
        sched = self._scheduler
        if gen_task.cancelled():
            return None, None, retry_count
        exc: BaseException | None = gen_task.exception()
        if exc is None:
            new_track: Path = gen_task.result()
            sched.complete_generation(new_track)
            await sched.kill_proc()
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
            await sched.kill_proc()
            sched.disable()
            return None, None, 0

        # Under max retries: start a new gen_task after backoff.
        await self._backoff_sleep(2 ** (retry_count - 1))
        new_gen_task = asyncio.create_task(sched.generate_track())
        sched.begin_generation()
        return None, new_gen_task, retry_count

    async def _handle_vibe_change(
        self,
        gen_task: asyncio.Task[Path] | None,
    ) -> tuple[asyncio.Task[Path] | None, Path | None]:
        """Handle a vibe-changed event during playback.

        Cancels any in-flight generation, handles replay, or starts new
        generation.  Returns ``(new_gen_task_or_none, replay_track_or_none)``.
        """
        sched = self._scheduler
        sched.changed.clear()
        if gen_task is not None and not gen_task.done():
            gen_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await gen_task

        # Replay: handler pre-set the track on the scheduler.
        if sched.replay:
            replay_track = sched.consume_replay()
            await sched.kill_proc()
            return None, replay_track

        sched.begin_generation()
        new_gen_task = asyncio.create_task(sched.generate_track())
        return new_gen_task, None

    async def _playback_wait_loop(
        self,
        proc: asyncio.subprocess.Process,
        current_track: Path,
        gen_task: asyncio.Task[Path] | None,
        retry_count: int,
    ) -> _PlaybackWaitResult:
        """Wait on a music subprocess, handling events until it should stop.

        Races the subprocess against ``changed`` and an optional
        in-flight generation task.  Handles music-off, generation completion
        (success and failure with retry), vibe changes, replay, and natural
        subprocess termination.

        Returns a :class:`_PlaybackWaitResult` describing the new state.
        The caller uses this to decide whether to respawn the subprocess,
        break out of the playback loop, or continue with a new track.
        """
        sched = self._scheduler

        while True:
            wait_task = asyncio.create_task(proc.wait())
            changed_task: asyncio.Task[bool] = asyncio.create_task(
                sched.changed.wait(),
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
            if sched.mode != "on":
                if gen_task is not None and not gen_task.done():
                    gen_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await gen_task
                    gen_task = None
                await sched.kill_proc()
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
            if sched.changed.is_set():
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
        sched = self._scheduler
        sleep_task = asyncio.create_task(asyncio.sleep(seconds))
        changed_task = asyncio.create_task(sched.changed.wait())
        _done, pending = await asyncio.wait(
            {sleep_task, changed_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await t
