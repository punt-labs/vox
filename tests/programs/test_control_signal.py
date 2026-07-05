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
from punt_vox.voxd.programs.lifecycle_signal import TurnOff, TurnOn, VibeStyleChange
from punt_vox.voxd.programs.playback_signal import PlayPart, Rotate, StartFromDisk

PartFactory = Callable[[int], Part]
PoolFactory = Callable[..., frozenset[Part]]
RotatingFactory = Callable[[PlaybackPolicy], Program]


class TestLifecycleSignals:
    def test_turn_on(self, policy: PlaybackPolicy) -> None:
        prog = Program(ProgramState.initial(), policy)
        TurnOn().apply(prog)
        assert prog.mode is Mode.GENERATING_FIRST

    def test_turn_off(
        self, make_rotating: RotatingFactory, policy: PlaybackPolicy
    ) -> None:
        prog = make_rotating(policy)
        TurnOff().apply(prog)
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
