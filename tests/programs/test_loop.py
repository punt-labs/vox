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
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Self, final

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

if TYPE_CHECKING:
    import pytest

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


@final
class _RaisingProcess:
    """A process whose wait() raises -- a player error, not a clean track end."""

    __slots__ = ("rc",)
    rc: int | None

    def __new__(cls) -> Self:
        self = super().__new__(cls)
        self.rc = None
        return self

    async def wait(self) -> int:
        msg = "transport gone"
        raise RuntimeError(msg)

    async def kill(self) -> None:
        self.rc = -9


@final
class _ErrThenBlockPlayer:
    """Hand out a wait-raising process first, then a process that never ends."""

    __slots__ = ("parts", "procs")
    parts: list[Part]
    procs: list[_RaisingProcess | FakeProcess]

    def __new__(cls) -> Self:
        self = super().__new__(cls)
        self.parts = []
        self.procs = []
        return self

    async def play(self, part: Part) -> _RaisingProcess | FakeProcess:
        self.parts.append(part)
        proc: _RaisingProcess | FakeProcess = (
            _RaisingProcess() if len(self.parts) == 1 else FakeProcess()
        )
        self.procs.append(proc)
        return proc


class TestPlayerWaitError:
    """F8: a raised player wait() is a player error, never a clean advance."""

    async def test_player_errored_flags_a_raised_wait(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        async def _boom() -> int:
            msg = "transport gone"
            raise RuntimeError(msg)

        task: asyncio.Task[int] = asyncio.ensure_future(_boom())
        with contextlib.suppress(RuntimeError):
            await task
        with caplog.at_level(logging.ERROR):
            errored = ProgramLoop._player_errored(task)
        assert errored is True
        assert any("player wait failed" in r.getMessage() for r in caplog.records)

    async def test_player_errored_passes_a_clean_wait(self) -> None:
        async def _clean() -> int:
            return 0

        task: asyncio.Task[int] = asyncio.ensure_future(_clean())
        await task
        assert ProgramLoop._player_errored(task) is False

    async def test_wait_error_kills_and_replays_without_advancing(
        self, rotating: Program, caplog: pytest.LogCaptureFixture
    ) -> None:
        player = _ErrThenBlockPlayer()
        channel = ControlChannel(rotating)
        loop = ProgramLoop(channel, player)
        serve = asyncio.create_task(channel.serve())
        run = asyncio.create_task(loop.run())
        with caplog.at_level(logging.ERROR):
            for _ in range(500):
                if len(player.parts) >= 2:
                    break
                await asyncio.sleep(0)
        for task in (run, serve):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        assert player.procs[0].rc == -9  # the errored player was killed, not ended
        assert (
            player.parts[1] == player.parts[0]
        )  # replayed, NOT advanced (no masquerade)
        assert any("player wait failed" in r.getMessage() for r in caplog.records)


@final
class _FailFirstPlayer:
    """Raise on the first play, then hand back a controllable process."""

    __slots__ = ("_calls", "parts", "procs")
    _calls: int
    parts: list[Part]
    procs: list[FakeProcess]

    def __new__(cls) -> Self:
        self = super().__new__(cls)
        self._calls = 0
        self.parts = []
        self.procs = []
        return self

    async def play(self, part: Part) -> FakeProcess:
        self._calls += 1
        if self._calls == 1:
            msg = "boom"
            raise RuntimeError(msg)
        self.parts.append(part)
        proc = FakeProcess()
        self.procs.append(proc)
        return proc


class TestLoopSurvivesAFailingStep:
    """The run() guard keeps playback alive when one step raises unexpectedly."""

    async def test_run_survives_and_plays_after_a_failing_step(
        self, rotating: Program, caplog: pytest.LogCaptureFixture
    ) -> None:
        player = _FailFirstPlayer()
        channel = ControlChannel(rotating)
        loop = ProgramLoop(channel, player)
        serve = asyncio.create_task(channel.serve())
        run = asyncio.create_task(loop.run())
        with caplog.at_level(logging.ERROR):
            for _ in range(500):
                if player.parts:
                    break
                await asyncio.sleep(0)
        for task in (run, serve):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        assert player.parts  # the loop recovered and played after the crash
        assert any(
            "unexpected error in a step" in r.getMessage() for r in caplog.records
        )
