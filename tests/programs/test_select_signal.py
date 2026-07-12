"""Tests for ``SwitchSelection`` and the generate-outcome lost-race no-op.

Asserts the switch invariants by name: a switch retargets the channel to a
consume-only ``SelectionPlayback`` (no fill armed); and a fill outcome applied
while a ``SelectionPlayback`` is active rejects via ``GuardViolationError`` and
mutates nothing -- the crash defense for an outcome that lands after the source
was switched. ``ControlChannel`` swallows the guard, so the writer survives.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from punt_vox.types_programs.identifiers import Reason
from punt_vox.voxd.programs import Part, Program, ProgramState
from punt_vox.voxd.programs.active_context import ActiveContext, ActiveSelection
from punt_vox.voxd.programs.control_channel import ControlChannel
from punt_vox.voxd.programs.fill_signal import (
    PermanentFailure,
    Produced,
    TransientFailure,
)
from punt_vox.voxd.programs.guard import GuardViolationError
from punt_vox.voxd.programs.rotate_policy import RotatePolicy
from punt_vox.voxd.programs.select_signal import SwitchSelection
from punt_vox.voxd.programs.selection import Selection
from punt_vox.voxd.programs.selection_playback import SelectionPlayback

from .conftest import AvoidRepeatPolicy


def _selection() -> Selection:
    return Selection.from_albums(
        [("album-a", (Part("001.mp3", 1), Part("002.mp3", 2)))]
    )


def _idle_channel() -> ControlChannel:
    return ControlChannel(Program(ProgramState.initial(), AvoidRepeatPolicy()))


def _replay() -> SelectionPlayback:
    return SelectionPlayback(_selection(), RotatePolicy())


class TestSwitchSelection:
    def test_interrupts(self) -> None:
        signal = SwitchSelection(
            _idle_channel(),
            ActiveContext(),
            _replay(),
            ActiveSelection(Path("/music"), _selection(), "radio"),
        )
        assert signal.interrupts is True

    def test_retargets_the_channel_to_the_replay(self) -> None:
        channel = _idle_channel()
        context = ActiveContext()
        playback = _replay()
        active = ActiveSelection(Path("/music"), _selection(), "radio")
        signal = SwitchSelection(channel, context, playback, active)

        signal.apply(channel.source)

        assert channel.source is playback
        assert context.current is active
        assert channel.source.playing is not None  # begins at the first track

    def test_replay_wants_no_generation(self) -> None:
        channel = _idle_channel()
        playback = _replay()
        SwitchSelection(
            channel,
            ActiveContext(),
            playback,
            ActiveSelection(Path("/music"), _selection(), "radio"),
        ).apply(channel.source)
        assert channel.source.wants_generation is False


class TestLostRaceNoOp:
    """A generate outcome applied while a replay is active is a benign lost race."""

    def test_produced_against_a_selection_rejects(self) -> None:
        outcome = Produced(Part("003.mp3", 3))
        with pytest.raises(GuardViolationError):
            outcome.apply(_replay())

    def test_permanent_failure_against_a_selection_rejects(self) -> None:
        outcome = PermanentFailure(Part("003.mp3", 3), Reason("bad"))
        with pytest.raises(GuardViolationError):
            outcome.apply(_replay())

    def test_transient_against_a_selection_rejects(self) -> None:
        with pytest.raises(GuardViolationError):
            TransientFailure(Reason("429")).apply(_replay())

    def test_replay_cursor_unchanged_after_a_dropped_outcome(self) -> None:
        replay = _replay()
        before = replay.playing
        with pytest.raises(GuardViolationError):
            Produced(Part("003.mp3", 3)).apply(replay)
        assert replay.playing == before  # mutated nothing

    async def test_channel_survives_a_fill_outcome_after_a_switch(self) -> None:
        # The switched-away race: a Produced posted after a SwitchSelection is
        # applied to the SelectionPlayback -- the channel swallows the guard and
        # the sole writer keeps running (no AttributeError, no crash).
        channel = _idle_channel()
        playback = _replay()
        channel.post(
            SwitchSelection(
                channel,
                ActiveContext(),
                playback,
                ActiveSelection(Path("/music"), _selection(), "radio"),
            )
        )
        channel.post(Produced(Part("003.mp3", 3)))
        await channel.apply_next()  # applies the switch
        await channel.apply_next()  # applies (and drops) the stale outcome
        assert channel.source is playback  # the writer survived
