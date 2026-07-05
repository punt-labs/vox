"""The playback loop -- play the current Part, auto-advance, race controls.

``ProgramLoop`` owns the player and nothing else: it plays ``program.playing``,
and when the track ends it *posts a Rotate message* (never mutating the Program
directly) so the single :class:`ControlChannel` writer advances the cursor, then
plays the new ``program.playing``. It never generates. A skip / play-a-part /
off interrupts the current track at once (the channel's ``interrupt`` event); a
retune does not -- the current track finishes first, then the loop plays the new
pool's Part (finish-current-then-switch). This closes the bas7 gap: the advance
is a real, listened-to transition, proven by a test that asserts the loop
spawned a *different* file on track-end.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Self, final

from punt_vox.voxd.programs.mode import Mode
from punt_vox.voxd.programs.playback_signal import Rotate

if TYPE_CHECKING:
    from punt_vox.voxd.programs.control_channel import ControlChannel
    from punt_vox.voxd.programs.part import Part
    from punt_vox.voxd.programs.player import Player, PlayerProcess

__all__ = ["ProgramLoop"]

logger = logging.getLogger(__name__)

_PLAYING_MODES = frozenset({Mode.PLAYING_FILLING, Mode.PLAYING_ROTATING, Mode.RETRYING})


@final
class ProgramLoop:
    """Play ``program.playing`` and advance when the track ends."""

    __slots__ = ("_channel", "_player")
    _channel: ControlChannel
    _player: Player

    def __new__(cls, channel: ControlChannel, player: Player) -> Self:
        self = super().__new__(cls)
        self._channel = channel
        self._player = player
        return self

    async def run(self) -> None:
        """Run the loop for the lifetime of the daemon.

        The top-level guard is the last line of defence: an unexpected error in
        one step (a raising player, a bug) is logged at ERROR and the loop
        continues, so playback never stops on a silent task death.
        """
        while True:
            try:
                await self._step()
            except Exception:
                logger.exception("playback loop: unexpected error in a step")

    async def _step(self) -> None:
        """Play the current Part, or wait until one becomes playable."""
        target = self._channel.program.playing
        if target is not None:
            await self._play(target)
            return
        await self._wait_for_playable()

    async def _wait_for_playable(self) -> None:
        """Block until a Part becomes playable (first track, or a retune)."""
        self._channel.changed.clear()
        if self._channel.program.playing is not None:
            return  # became available between the read and the clear
        await self._channel.changed.wait()

    async def _play(self, target: Part) -> None:
        """Play ``target``: race its end against an interrupt, then advance."""
        self._channel.interrupt.clear()
        proc = await self._player.play(target)
        if await self._interrupted(proc):
            await proc.kill()
        else:
            await self._advance_after(target)

    async def _interrupted(self, proc: PlayerProcess) -> bool:
        """Return True if the track did not end cleanly (interrupt or player error).

        A user interrupt wins outright. Otherwise the player's ``wait`` settled:
        a clean exit is a natural end (advance), but a *raised* ``wait`` is a
        player error -- retrieved and logged here so it never leaks as an
        unretrieved exception nor masquerades as a normal advance.
        """
        wait_task = asyncio.ensure_future(proc.wait())
        interrupt_task = asyncio.ensure_future(self._channel.interrupt.wait())
        done, pending = await asyncio.wait(
            {wait_task, interrupt_task}, return_when=asyncio.FIRST_COMPLETED
        )
        await self._cancel_all(pending)
        player_errored = wait_task in done and self._player_errored(wait_task)
        return interrupt_task in done or player_errored

    @staticmethod
    async def _cancel_all(tasks: set[asyncio.Task[int]]) -> None:
        """Cancel and reap the losing tasks of the interrupt race."""
        for task in tasks:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    @staticmethod
    def _player_errored(wait_task: asyncio.Task[int]) -> bool:
        """Retrieve the settled ``wait``; a raised one is an error, not a clean end."""
        exc = wait_task.exception()
        if exc is None:
            return False
        logger.error("player wait failed; not a clean track end", exc_info=exc)
        return True

    async def _advance_after(self, target: Part) -> None:
        """After a natural track end, post the advance (or play the retune target).

        If ``playing`` is still the track that just ended, post a Rotate and
        wait for the single writer to apply it; the loop then plays the advanced
        Part. If ``playing`` already changed (a retune finished mid-track), the
        loop simply re-reads and plays the new pool's Part -- no advance.
        """
        prog = self._channel.program
        if prog.playing == target and prog.mode in _PLAYING_MODES:
            self._channel.changed.clear()
            self._channel.post(Rotate())
            await self._channel.changed.wait()
