"""Tests for the typed ControlSignal command objects."""

from __future__ import annotations

from collections.abc import Callable

from punt_vox.voxd.programs import (
    Format,
    Mode,
    Part,
    PlaybackPolicy,
    Program,
    ProgramState,
)
from punt_vox.voxd.programs.active_context import ActiveContext
from punt_vox.voxd.programs.control_channel import ControlChannel
from punt_vox.voxd.programs.lifecycle_signal import TurnOff, TurnOn, VibeStyleChange
from punt_vox.voxd.programs.playback_signal import PlayPart, Rotate, StartFromDisk

from .conftest import AvoidRepeatPolicy

PartFactory = Callable[[int], Part]
PoolFactory = Callable[..., frozenset[Part]]
RotatingFactory = Callable[[PlaybackPolicy], Program]


def _off(program: Program | None = None) -> TurnOff:
    """Build a source-agnostic TurnOff (idle program used only for the replay path)."""
    base = program or Program(ProgramState.initial(), AvoidRepeatPolicy())
    idle = Program(ProgramState.initial(), AvoidRepeatPolicy())
    return TurnOff(ControlChannel(base), ActiveContext(), idle)


class TestLifecycleSignals:
    def test_turn_on(self, policy: PlaybackPolicy) -> None:
        prog = Program(ProgramState.initial(), policy)
        TurnOn().apply(prog)
        assert prog.mode is Mode.GENERATING_FIRST

    def test_turn_off(
        self, make_rotating: RotatingFactory, policy: PlaybackPolicy
    ) -> None:
        prog = make_rotating(policy)
        _off(prog).apply(prog)
        assert prog.mode is Mode.OFF

    def test_vibe_style_change(
        self,
        make_rotating: RotatingFactory,
        policy: PlaybackPolicy,
        pool_of: PoolFactory,
    ) -> None:
        prog = make_rotating(policy)
        VibeStyleChange(pool_of(20, 21)).apply(prog)
        assert prog.mode is Mode.PLAYING_FILLING
        assert {p.index for p in prog.pool} == {20, 21}


class TestPlaybackSignals:
    def test_rotate(self, rotating: Program) -> None:
        before = rotating.playing
        Rotate().apply(rotating)
        assert rotating.playing != before

    def test_play_part(self, rotating: Program, mk: PartFactory) -> None:
        PlayPart(mk(5)).apply(rotating)
        assert rotating.playing == mk(5)

    def test_start_from_disk(
        self, policy: PlaybackPolicy, mk: PartFactory, pool_of: PoolFactory
    ) -> None:
        prog = Program(ProgramState.restored(Format.PLAYLIST, pool_of(1, 2, 3)), policy)
        StartFromDisk(mk(2)).apply(prog)
        assert prog.mode is Mode.PLAYING_FILLING
        assert prog.playing == mk(2)
        assert prog.state.filling is False


class TestInterrupts:
    def test_interrupting_commands(self, mk: PartFactory) -> None:
        assert _off().interrupts is True
        assert Rotate().interrupts is True
        assert PlayPart(mk(1)).interrupts is True

    def test_non_interrupting_commands(
        self, mk: PartFactory, pool_of: PoolFactory
    ) -> None:
        assert TurnOn().interrupts is False
        assert VibeStyleChange(pool_of(1)).interrupts is False
        assert StartFromDisk(mk(1)).interrupts is False
