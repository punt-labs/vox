"""Tests for the single-writer ControlChannel, including the O2 concurrency contract."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from pathlib import Path
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
from punt_vox.voxd.programs.filesystem_store import FilesystemProgramStore
from punt_vox.voxd.programs.filler import Filler, FillPlan
from punt_vox.voxd.programs.lifecycle_signal import TurnOff, TurnOn, VibeStyleChange
from punt_vox.voxd.programs.manifest import PlaylistSubject, ProgramManifest
from punt_vox.voxd.programs.playback_signal import Rotate
from punt_vox.voxd.programs.producer import PartSpec
from punt_vox.voxd.programs.sleeper import Sleeper

PoolFactory = Callable[..., frozenset[Part]]
RotatingFactory = Callable[[PlaybackPolicy], Program]
ManifestFactory = Callable[..., ProgramManifest]


async def _drain(channel: ControlChannel) -> None:
    """Run the consumer until the queue is empty, then stop it."""
    server = asyncio.create_task(channel.serve())
    await channel.join()
    server.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await server


class TestSingleWriter:
    async def test_applies_a_command(self, policy: PlaybackPolicy) -> None:
        channel = ControlChannel(Program(ProgramState.initial(), policy))
        channel.post(TurnOn())
        await channel.apply_next()
        assert channel.program.mode is Mode.GENERATING_FIRST

    async def test_changed_event_set_after_apply(self, policy: PlaybackPolicy) -> None:
        channel = ControlChannel(Program(ProgramState.initial(), policy))
        channel.post(TurnOn())
        await channel.apply_next()
        assert channel.changed.is_set()

    async def test_lost_race_guard_is_swallowed(self, policy: PlaybackPolicy) -> None:
        # turn_on twice: the second is a lost race (already on) -> ValueError,
        # swallowed so the single writer survives and the state is unchanged.
        channel = ControlChannel(Program(ProgramState.initial(), policy))
        channel.post(TurnOn())
        channel.post(TurnOn())
        await _drain(channel)
        assert channel.program.mode is Mode.GENERATING_FIRST

    async def test_serves_a_batch_in_order(
        self,
        make_rotating: RotatingFactory,
        policy: PlaybackPolicy,
        pool_of: PoolFactory,
    ) -> None:
        channel = ControlChannel(make_rotating(policy))
        channel.post(Rotate())
        channel.post(VibeStyleChange(pool_of(20, 21)))
        await _drain(channel)
        # Vibe applied last -> the pool is the retuned one.
        assert {p.index for p in channel.program.pool} == {20, 21}


class TestO2Concurrency:
    """Concurrent next + vibe never interleave: the result is one of the two
    valid sequential outcomes, and the Program is always a legal state."""

    async def test_concurrent_next_and_vibe_is_sequential(
        self,
        make_rotating: RotatingFactory,
        policy: PlaybackPolicy,
        pool_of: PoolFactory,
    ) -> None:
        new_pool = pool_of(20, 21)

        def sequential(*signals: ControlSignal) -> ProgramState:
            prog = make_rotating(policy)
            for signal in signals:
                signal.apply(prog)
            return prog.state

        rotate_then_vibe = sequential(Rotate(), VibeStyleChange(new_pool))
        vibe_then_rotate = sequential(VibeStyleChange(new_pool), Rotate())

        channel = ControlChannel(make_rotating(policy))
        server = asyncio.create_task(channel.serve())
        # Two "clients" fire concurrently; the channel serializes them.
        await asyncio.gather(
            _post_soon(channel, Rotate()),
            _post_soon(channel, VibeStyleChange(new_pool)),
        )
        await channel.join()
        server.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await server

        result = channel.program.state
        assert result in {rotate_then_vibe, vibe_then_rotate}  # never interleaved
        # The pool is the retuned one under both orderings (rotate never repools).
        assert {p.index for p in channel.program.pool} == {20, 21}


async def _post_soon(channel: ControlChannel, signal: ControlSignal) -> None:
    await asyncio.sleep(0)
    channel.post(signal)


@final
class _HoldingProducer:
    """Never completes -- keeps the fill task alive so is_running stays True."""

    async def produce(self, spec: PartSpec, target: Path) -> Part:
        await asyncio.Event().wait()  # blocks forever
        return Part(target.name, spec.index)


@final
class _FixedPlanSource:
    __slots__ = ("_plan",)
    _plan: FillPlan

    def __new__(cls, plan: FillPlan) -> Self:
        self = super().__new__(cls)
        self._plan = plan
        return self

    def current_plan(self) -> FillPlan:
        return self._plan


class TestFillReconciliation:
    """The consumer starts/cancels the Filler to match program.filling."""

    async def test_filling_starts_and_off_cancels_the_fill(
        self,
        tmp_path: Path,
        policy: PlaybackPolicy,
        sleeper: Sleeper,
        manifest_of: ManifestFactory,
    ) -> None:
        store = FilesystemProgramStore(tmp_path).create(manifest_of("prog"))
        plan = FillPlan(store, PlaylistSubject(vibe="calm", style="jazz"), ("p",))
        channel = ControlChannel(Program(ProgramState.initial(), policy))
        filler = Filler(_HoldingProducer(), channel, sleeper)
        channel.attach_fill(filler, _FixedPlanSource(plan))

        channel.post(TurnOn())  # empty pool -> generating_first -> filling True
        await channel.apply_next()
        assert filler.is_running  # the consumer started the fill

        channel.post(TurnOff())  # -> off -> filling False
        await channel.apply_next()
        assert not filler.is_running  # the consumer cancelled the fill
