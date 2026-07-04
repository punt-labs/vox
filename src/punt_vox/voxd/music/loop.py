"""Playback loop: auto-advance on track-end, control racing, vibe finish-switch.

The loop owns the player subprocess and nothing else. It plays the current
track, races the subprocess against the scheduler's control signal, and on the
subprocess ending selects the next track through the scheduler's pure decision
(auto-advance). Generation lives entirely in the scheduler-owned
:class:`~punt_vox.voxd.music.filler.PoolFiller`; the loop never generates.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Literal, Self, cast

from punt_vox.voxd.music.playback_cmd import music_player_command

if TYPE_CHECKING:
    from pathlib import Path

    from punt_vox.voxd.music.scheduler import MusicScheduler

__all__ = [
    "MusicLoop",
]

logger = logging.getLogger(__name__)

# The next thing the loop does once the current player stops.
_Action = Literal["off", "skip", "play", "ended", "switch"]


class MusicLoop:
    """Play the current track and auto-advance when it ends."""

    __slots__ = ("_scheduler",)
    _scheduler: MusicScheduler

    def __new__(cls, scheduler: MusicScheduler) -> Self:
        self = super().__new__(cls)
        self._scheduler = scheduler
        return self

    async def run(self) -> None:
        """Run the music loop for the lifetime of the daemon."""
        while True:
            await self._scheduler.wait_active()
            await self._run_session()

    async def _run_session(self) -> None:
        """Play one session: enter the pool, then auto-advance until off."""
        sched = self._scheduler
        # Consume the turn-on wake signal so it is not misread as a mid-play
        # control by the first _supervise.
        sched.take_control()
        sched.changed.clear()
        try:
            current = await self._first_track()
        except (RuntimeError, OSError):
            logger.exception("Music could not start; disabling")
            sched.disable()
            return

        while current is not None and sched.mode == "on":
            proc = await self._spawn(current)
            action = await self._supervise(proc)
            if action == "off" or sched.mode != "on":
                return
            current = await self._next_track(action)

    async def _first_track(self) -> Path | None:
        """Return the first track to play: a queued named track, else the pool."""
        sched = self._scheduler
        if sched.has_pending_track:
            return sched.take_pending_track()
        return await self._enter_pool()

    async def _enter_pool(self) -> Path | None:
        """Return the first track for the current pool, or None if off.

        A non-empty pool plays a member immediately; an empty pool waits for
        the fill to deliver the first track while staying responsive to
        off / vibe / play (generating-first).
        """
        sched = self._scheduler
        sched.ensure_fill()
        if not sched.pool_empty:
            return sched.select_first()
        sched.mark_generating()
        return await self._await_first()

    async def _await_first(self) -> Path | None:
        """Wait for the fill's first track, staying responsive to controls."""
        sched = self._scheduler
        while True:
            first = asyncio.ensure_future(sched.await_first_track())
            signalled = asyncio.ensure_future(sched.changed.wait())
            done, pending = await asyncio.wait(
                {first, signalled}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            if first in done:
                return first.result()  # raises if the fill exhausted retries
            sched.changed.clear()
            control = sched.take_control()
            if control == "off":
                return None
            if control == "play":
                return sched.take_pending_track()
            if control in {"skip", "vibe"}:
                sched.ensure_fill()
                if not sched.pool_empty:
                    # skip (advance now) and vibe (retarget) both want an on-disk
                    # track the instant one exists: play it. ensure_fill covers the
                    # vibe case where the pool identity changed; awaiting a first
                    # generation a full pool never starts would hang forever.
                    return sched.select_first()
                # Empty pool: loop and re-race the first track. skip never reaches
                # here (skip_next no-ops on an empty pool, Z finding #1); only a
                # vibe retarget onto a still-empty pool falls through.

    async def _spawn(self, track: Path) -> asyncio.subprocess.Process:
        """Mark ``track`` playing and start its player subprocess."""
        sched = self._scheduler
        sched.mark_playing(track)
        cmd = music_player_command(track)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        sched.begin_playback(proc)
        return proc

    async def _supervise(self, proc: asyncio.subprocess.Process) -> _Action:
        """Wait for the player to end or a control to fire, and return the action.

        A vibe change does not interrupt playback (finish-current-first): it
        sets a pending switch and keeps waiting, so the current song finishes
        and the next track comes from the new pool. off / skip / play kill the
        player at once.
        """
        sched = self._scheduler
        pending_switch = False
        while True:
            if not await self._control_fired(proc):
                await self._log_exit(proc)
                return "switch" if pending_switch else "ended"
            sched.changed.clear()
            control = sched.take_control()
            if control == "vibe":
                pending_switch = True
                continue
            if control in {"off", "skip", "play"}:
                await sched.kill_proc()
                return cast("_Action", control)
            # "none": spurious wake -- keep waiting on the same player.

    async def _control_fired(self, proc: asyncio.subprocess.Process) -> bool:
        """Race the player against the control signal; True if a control fired."""
        wait_task = asyncio.ensure_future(proc.wait())
        signal_task = asyncio.ensure_future(self._scheduler.changed.wait())
        done, pending = await asyncio.wait(
            {wait_task, signal_task}, return_when=asyncio.FIRST_COMPLETED
        )
        control_fired = signal_task in done
        for task in pending:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        return control_fired

    async def _next_track(self, action: _Action) -> Path | None:
        """Select the track to play after the current one stops."""
        sched = self._scheduler
        if action in {"skip", "ended"}:
            return sched.select_next_track()
        if action == "play":
            return sched.take_pending_track()
        # "switch": the vibe changed and the current song finished.
        return await self._enter_pool()

    @staticmethod
    async def _log_exit(proc: asyncio.subprocess.Process) -> None:
        """Log a warning if the player subprocess exited with an error."""
        rc = proc.returncode
        if rc is not None and rc != 0:
            stderr_text = ""
            if proc.stderr is not None:
                stderr_bytes = await proc.stderr.read()
                stderr_text = stderr_bytes.decode(errors="replace").strip()
            logger.warning("Music playback ended with rc=%s: %s", rc, stderr_text)
