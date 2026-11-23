"""Tests for the voice call start fields."""

from pathlib import Path

import pytest
import typer

from punt_vox.commands.voice_call_options import VoiceCallOptions


def test_options_cannot_combine_script_and_echo_control(tmp_path: Path) -> None:
    with pytest.raises(typer.BadParameter, match="apply only to live calls"):
        VoiceCallOptions(tmp_path / "call.jsonl", "headset")


@pytest.mark.parametrize(
    ("headset", "aec", "fields"),
    [
        (False, False, {}),
        (True, False, {"echo_control": "headset"}),
        (False, True, {"echo_control": "aec"}),
    ],
)
def test_live_options_serialize_echo_declaration(
    headset: bool, aec: bool, fields: dict[str, object]
) -> None:
    assert VoiceCallOptions.from_flags(None, headset=headset, aec=aec).wire() == fields


def test_script_options_serialize_recording(tmp_path: Path) -> None:
    script = tmp_path / "call.jsonl"
    script.write_text('{"pcm_path": "turn.pcm", "pause_s": 0.5}\n', encoding="utf-8")
    assert VoiceCallOptions.from_flags(script, headset=False, aec=False).wire() == {
        "script": [{"pcm_path": str(tmp_path / "turn.pcm"), "pause_s": 0.5}]
    }
