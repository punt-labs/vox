"""Tests for the ``SwitchProgram`` control signal -- atomic source swap."""

from __future__ import annotations

from pathlib import Path

from punt_vox.types_programs import Format, Mode
from punt_vox.types_programs.prompts import PromptSet
from punt_vox.voxd.programs import Program, ProgramState
from punt_vox.voxd.programs.active_context import ActiveContext, ActiveProgram
from punt_vox.voxd.programs.album_id import AlbumId
from punt_vox.voxd.programs.album_tags import AlbumTags
from punt_vox.voxd.programs.control_channel import ControlChannel
from punt_vox.voxd.programs.switch_signal import SwitchProgram

from .conftest import AvoidRepeatPolicy, InMemoryPartStore, make_manifest, make_part


def _idle_channel() -> ControlChannel:
    return ControlChannel(Program(ProgramState.initial(), AvoidRepeatPolicy()))


def _active(directory: str = "saved-a3f1c9") -> ActiveProgram:
    return ActiveProgram(
        album_id=AlbumId("a3f1c9"),
        store=InMemoryPartStore(make_manifest(1, 2)),
        tags=AlbumTags(style="techno", vibe="ambient"),
        directory=Path("/music") / directory,
        prompts=PromptSet(base="p", variations=()),
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

        signal.apply(channel.source)

        assert channel.source is new_program
        assert context.current is active
        assert new_program.playing == make_part(1)
        assert new_program.state.filling is False  # cold start, no fill

    def test_turn_on_generates_from_an_empty_pool(self) -> None:
        channel = _idle_channel()
        new_program = _seeded()  # empty
        signal = SwitchProgram(channel, ActiveContext(), new_program, _active(), None)

        signal.apply(channel.source)

        assert channel.source is new_program
        assert new_program.mode is Mode.GENERATING_FIRST
        assert new_program.state.filling is True

    def test_turn_on_plays_a_resumed_pool_at_once(self) -> None:
        channel = _idle_channel()
        new_program = _seeded(1, 2, 3)
        signal = SwitchProgram(channel, ActiveContext(), new_program, _active(), None)

        signal.apply(channel.source)

        assert new_program.mode is Mode.PLAYING_FILLING
        assert new_program.playing is not None

    def test_switch_replaces_a_previously_active_source(self) -> None:
        channel = _idle_channel()
        first = _seeded(1, 2)
        SwitchProgram(channel, ActiveContext(), first, _active("a-1"), None).apply(
            channel.source
        )
        second = _seeded(5, 6)
        SwitchProgram(
            channel, ActiveContext(), second, _active("b-2"), make_part(5)
        ).apply(channel.source)
        assert channel.source is second
        assert channel.source.playing == make_part(5)
