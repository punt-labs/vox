"""Tests for the Mode B start fields."""

from pathlib import Path

import pytest

from punt_vox.commands.voice_call_options import VoiceCallOptions


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
