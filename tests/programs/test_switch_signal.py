"""Tests for the ``SwitchProgram`` control signal -- atomic Program swap."""

from __future__ import annotations

from pathlib import Path

from punt_vox.voxd.programs import Format, Mode, Program, ProgramState
from punt_vox.voxd.programs.active_context import ActiveContext, ActiveProgram
from punt_vox.voxd.programs.control_channel import ControlChannel
from punt_vox.voxd.programs.identifiers import ProgramName
from punt_vox.voxd.programs.manifest import PlaylistSubject
from punt_vox.voxd.programs.switch_signal import SwitchProgram

from .conftest import AvoidRepeatPolicy, InMemoryPartStore, make_manifest, make_part


def _idle_channel() -> ControlChannel:
    return ControlChannel(Program(ProgramState.initial(), AvoidRepeatPolicy()))


def _active(name: str = "saved") -> ActiveProgram:
    return ActiveProgram(
        name=ProgramName(name),
        store=InMemoryPartStore(make_manifest(name, 1, 2)),
        subject=PlaylistSubject(vibe="ambient", style="techno"),
        directory=Path("/music") / name,
        prompts=("p",),
    )


def _seeded(*indices: int) -> Program:
    pool = frozenset(make_part(i) for i in indices)
    return Program(ProgramState.restored(Format.PLAYLIST, pool), AvoidRepeatPolicy())


class TestSwitchProgram:
    def test_interrupts_current_playback(self) -> None:
        assert SwitchProgram(
            _idle_channel(), ActiveContext(), _seeded(), _active(), None
        ).interrupts

    def test_play_target_cold_starts_from_disk(self) -> None:
        channel = _idle_channel()
        context = ActiveContext()
        new_program = _seeded(1, 2)
        active = _active()
        signal = SwitchProgram(channel, context, new_program, active, make_part(1))

        signal.apply(channel.program)

        assert channel.program is new_program
        assert context.current is active
        assert new_program.playing == make_part(1)
        assert new_program.state.filling is False  # cold start, no fill (finding #2)

    def test_turn_on_generates_from_an_empty_pool(self) -> None:
        channel = _idle_channel()
        context = ActiveContext()
        new_program = _seeded()  # empty
        signal = SwitchProgram(channel, context, new_program, _active(), None)

        signal.apply(channel.program)

        assert channel.program is new_program
        assert new_program.mode is Mode.GENERATING_FIRST
        assert new_program.state.filling is True

    def test_turn_on_plays_a_resumed_pool_at_once(self) -> None:
        channel = _idle_channel()
        new_program = _seeded(1, 2, 3)
        signal = SwitchProgram(channel, ActiveContext(), new_program, _active(), None)

        signal.apply(channel.program)

        assert new_program.mode is Mode.PLAYING_FILLING
        assert new_program.playing is not None

    def test_switch_replaces_a_previously_active_program(self) -> None:
        channel = _idle_channel()
        first = _seeded(1, 2)
        SwitchProgram(channel, ActiveContext(), first, _active("a"), None).apply(
            channel.program
        )
        second = _seeded(5, 6)
        SwitchProgram(
            channel, ActiveContext(), second, _active("b"), make_part(5)
        ).apply(channel.program)
        assert channel.program is second
        assert channel.program.playing == make_part(5)
