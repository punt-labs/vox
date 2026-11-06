"""Tests for :class:`CallWiring`: one immutable bundle both drive loops share."""

from __future__ import annotations

import dataclasses
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from punt_vox.commands.call_scripted import ScriptedCallDriver
from punt_vox.commands.call_wiring import CallWiring
from punt_vox.voxd.conversation_mode.call_control import CallControl
from punt_vox.voxd.conversation_mode.call_session import CallSession
from punt_vox.voxd.conversation_mode.reply import ReplyChunk
from punt_vox.voxd.conversation_mode.speak_fn import SpeakFn
from punt_vox.voxd.conversation_mode.turn import TranscribedTurn
from punt_vox.voxd.conversation_mode.turn_timer import TurnStage


class _Attach:
    async def send_turn(self, turn: TranscribedTurn) -> AsyncIterator[ReplyChunk]:
        del turn
        yield ReplyChunk(text="", is_final=True)


class _Timer:
    def __init__(self) -> None:
        self.stages: list[TurnStage] = []

    def mark(self, stage: TurnStage, *, detail: str | None = None) -> None:
        del detail
        self.stages.append(stage)


async def _speak(text: str) -> None:
    del text


async def _chime() -> None:
    return None


async def _never_stop(
    control: CallControl, session: CallSession, speak: SpeakFn
) -> bool:
    del control, session, speak
    return False


def _wiring(tmp_path: Path, timer: _Timer) -> CallWiring:
    return CallWiring(
        session_attach=_Attach(),
        speak=_speak,
        chime=_chime,
        control=CallControl(tmp_path / "call.control"),
        apply_control=_never_stop,
        turn_timer=timer,
    )


@pytest.mark.parametrize("field", [f.name for f in dataclasses.fields(CallWiring)])
def test_the_wiring_is_immutable(tmp_path: Path, field: str) -> None:
    wiring = _wiring(tmp_path, _Timer())
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(wiring, field, None)


def test_the_wiring_carries_exactly_what_it_was_given(tmp_path: Path) -> None:
    timer = _Timer()
    wiring = _wiring(tmp_path, timer)
    assert (wiring.speak, wiring.chime, wiring.turn_timer) == (_speak, _chime, timer)


async def test_a_drive_loop_marks_the_wiring_s_one_timer(tmp_path: Path) -> None:
    """The call's stages land on the wiring's timer, not a second clock."""
    timer = _Timer()
    script = tmp_path / "script.jsonl"
    script.write_text('{"text": "what does this do", "confidence": 0.95}\n')
    await ScriptedCallDriver.create(script, _wiring(tmp_path, timer)).run()
    assert "turn_sent" in timer.stages
