"""Behavioral-parity snapshot for the playback loop (ports tests/music/test_loop.py).

Every test drives the REAL loop + the REAL ControlChannel consumer with a fake
player whose process end the test controls. Assertions are on what the loop
actually *spawned* -- a different file on track-end (the bas7 gap), no advance
past a retune's finish, the current player surviving a retune, the player killed
on off/skip -- and on the Program mode, never on removed scheduler internals.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from typing import Self, final

from punt_vox.voxd.programs import (
    Mode,
    Part,
    PlaybackPolicy,
    Program,
    ProgramState,
)
from punt_vox.voxd.programs.control_channel import ControlChannel
from punt_vox.voxd.programs.control_signal import ControlSignal
from punt_vox.voxd.programs.fill_signal import Produced
from punt_vox.voxd.programs.lifecycle_signal import TurnOff, TurnOn, VibeStyleChange
from punt_vox.voxd.programs.loop import ProgramLoop
from punt_vox.voxd.programs.playback_signal import Rotate

PoolFactory = Callable[..., frozenset[Part]]
RotatingFactory = Callable[[PlaybackPolicy], Program]


@final
class FakeProcess:
    """A fake player process whose end the test controls."""

    __slots__ = ("_ended", "rc")
    rc: int | None
    _ended: asyncio.Event

    def __new__(cls) -> Self:
        self = super().__new__(cls)
        self.rc = None
        self._ended = asyncio.Event()
        return self

    async def wait(self) -> int:
        await self._ended.wait()
        return self.rc if self.rc is not None else 0

    async def kill(self) -> None:
        self.rc = -9
        self._ended.set()

    def end(self, rc: int = 0) -> None:
        """Signal a natural end (test control)."""
        self.rc = rc
        self._ended.set()


@final
class FakePlayer:
    """Record the Parts played and hand back controllable processes."""

    __slots__ = ("_spawned", "parts", "procs")
    parts: list[Part]
    procs: list[FakeProcess]
    _spawned: asyncio.Event

    def __new__(cls) -> Self:
        self = super().__new__(cls)
        self.parts = []
        self.procs = []
        self._spawned = asyncio.Event()
        return self

    async def play(self, part: Part) -> FakeProcess:
        self.parts.append(part)
        proc = FakeProcess()
        self.procs.append(proc)
        self._spawned.set()
        return proc

    async def wait_for(self, count: int) -> None:
        while len(self.parts) < count:
            self._spawned.clear()
            if len(self.parts) >= count:
                return
            await asyncio.wait_for(self._spawned.wait(), timeout=2.0)


async def _settle() -> None:
    for _ in range(10):
        await asyncio.sleep(0)


class _Harness:
    """A running channel consumer + loop, torn down together."""

    __slots__ = ("_loop", "_serve", "channel", "player")
    channel: ControlChannel
    player: FakePlayer
    _serve: asyncio.Task[None]
    _loop: asyncio.Task[None]

    def __new__(cls, program: Program) -> Self:
        self = super().__new__(cls)
        self.channel = ControlChannel(program)
        self.player = FakePlayer()
        loop = ProgramLoop(self.channel, self.player)
        self._serve = asyncio.create_task(self.channel.serve())
        self._loop = asyncio.create_task(loop.run())
        return self

    async def stop(self) -> None:
        for task in (self._loop, self._serve):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


class TestAutoAdvance:
    async def test_track_end_advances_to_different_track(
        self, rotating: Program
    ) -> None:
        harness = _Harness(rotating)
        await harness.player.wait_for(1)
        first = harness.player.parts[0]
        harness.player.procs[0].end()  # natural end -- no skip
        await harness.player.wait_for(2)
        assert harness.player.parts[1] != first  # auto-advanced to a different Part
        await harness.stop()

    async def test_full_pool_rotates_without_repeat(self, rotating: Program) -> None:
        harness = _Harness(rotating)
        await harness.player.wait_for(1)
        harness.player.procs[0].end()
        await harness.player.wait_for(2)
        harness.player.procs[1].end()
        await harness.player.wait_for(3)
        assert harness.player.parts[0] != harness.player.parts[1]
        assert harness.player.parts[1] != harness.player.parts[2]
        await harness.stop()


class TestSingleTrackLoops:
    async def test_track_end_replays_sole_part(self, policy: PlaybackPolicy) -> None:
        prog = Program(ProgramState.initial(), policy)
        prog.turn_on()
        prog.first_track_ok(Part("id001", 1))  # playing_filling, pool of one
        harness = _Harness(prog)
        await harness.player.wait_for(1)
        harness.player.procs[0].end()
        await harness.player.wait_for(2)
        assert harness.player.parts[1] == harness.player.parts[0]  # looped the one Part
        await harness.stop()


class TestRetuneFinishesThenSwitches:
    async def test_current_survives_then_switches_pool(
        self, rotating: Program, pool_of: PoolFactory
    ) -> None:
        harness = _Harness(rotating)
        await harness.player.wait_for(1)
        harness.channel.post(VibeStyleChange(pool_of(20, 21)))  # retune (no interrupt)
        await _settle()
        assert harness.player.procs[0].rc is None  # current NOT killed
        harness.player.procs[0].end()  # current finishes
        await harness.player.wait_for(2)
        assert harness.player.parts[1].index in {20, 21}  # switched to the new pool
        await harness.stop()


class TestOffInterrupts:
    async def test_off_kills_current_and_stops(self, rotating: Program) -> None:
        harness = _Harness(rotating)
        await harness.player.wait_for(1)
        harness.channel.post(TurnOff())  # interrupts
        await _settle()
        assert harness.player.procs[0].rc == -9  # player killed, not ended
        assert harness.channel.program.mode is Mode.OFF
        await harness.stop()


class TestSkipInterrupts:
    async def test_skip_kills_current_and_plays_next(self, rotating: Program) -> None:
        harness = _Harness(rotating)
        await harness.player.wait_for(1)
        first = harness.player.parts[0]
        harness.channel.post(Rotate())  # a user skip interrupts
        await harness.player.wait_for(2)
        assert harness.player.procs[0].rc == -9  # killed mid-track
        assert harness.player.parts[1] != first
        await harness.stop()


class TestGeneratingFirstThenPlays:
    async def test_empty_pool_awaits_then_plays_first_track(
        self, policy: PlaybackPolicy
    ) -> None:
        harness = _Harness(Program(ProgramState.initial(), policy))
        harness.channel.post(TurnOn())  # empty pool -> generating_first
        await _settle()
        assert harness.player.parts == []  # nothing plays before the first track
        assert harness.channel.program.mode is Mode.GENERATING_FIRST
        harness.channel.post(Produced(Part("id001", 1)))  # the fill delivers #1
        await harness.player.wait_for(1)
        assert harness.player.parts[0] == Part("id001", 1)
        await harness.stop()


class TestConcurrentControlsStaySequential:
    """O2: concurrent skip + retune + a fill completion never corrupt the loop."""

    async def test_concurrent_next_vibe_and_fill(
        self,
        make_rotating: RotatingFactory,
        policy: PlaybackPolicy,
        pool_of: PoolFactory,
    ) -> None:
        harness = _Harness(make_rotating(policy))
        await harness.player.wait_for(1)
        # Three clients fire at once through the single writer.
        await asyncio.gather(
            _post(harness.channel, Rotate()),
            _post(harness.channel, VibeStyleChange(pool_of(20, 21))),
            _post(harness.channel, Produced(Part("id020", 20))),
        )
        await harness.channel.join()
        prog = harness.channel.program
        # The Program is always a legal state; the last retune won the pool.
        assert {p.index for p in prog.pool} <= {20, 21}
        assert prog.mode in {Mode.PLAYING_FILLING, Mode.PLAYING_ROTATING}
        await harness.stop()


async def _post(channel: ControlChannel, signal: ControlSignal) -> None:
    await asyncio.sleep(0)
    channel.post(signal)
