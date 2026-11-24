from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, cast

import pytest

from punt_vox.voxd.conversation_mode.audio_chunk import AudioChunk
from punt_vox.voxd.voice_call.inputs import MicInput, ScriptInput, ScriptTurn

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from punt_vox.voxd.conversation_mode.mic_audio_source import MicAudioSource


async def test_script_speaks_padded_frames_then_silence() -> None:
    script = ScriptInput((ScriptTurn(b"\x01" * 700, 0.04),))
    stream = script.chunks()
    frames = [await anext(stream) for _ in range(5)]
    assert [len(f) for f in frames] == [640] * 5
    assert frames[0] == b"\x01" * 640
    assert frames[1] == b"\x01" * 60 + bytes(580)
    assert frames[2:] == [bytes(640)] * 3


async def test_script_is_paced_in_real_time() -> None:
    stream = ScriptInput((ScriptTurn(bytes(640 * 10), 0.0),)).chunks()
    loop = asyncio.get_running_loop()
    started = loop.time()
    for _ in range(10):
        await anext(stream)
    assert loop.time() - started >= 0.18


def test_from_wire_reads_turn_files(tmp_path: Path) -> None:
    pcm = tmp_path / "hi.pcm"
    pcm.write_bytes(b"\x02" * 64)
    script = ScriptInput.from_wire([{"pcm_path": str(pcm), "pause_s": 1.5}])
    assert isinstance(script, ScriptInput)


@pytest.mark.parametrize(
    "raw",
    [
        [],
        "x",
        [1],
        [{"pcm_path": "rel.pcm"}],
        [{"pcm_path": "/x", "pause_s": -1}],
        [{"pcm_path": "/dev/zero"}],
        [{"pcm_path": "/tmp"}],
    ],
)
def test_from_wire_refuses_malformed_scripts(raw: object) -> None:
    with pytest.raises(ValueError):
        ScriptInput.from_wire(raw)


def test_from_wire_caps_the_number_of_turns(tmp_path: Path) -> None:
    pcm = tmp_path / "hi.pcm"
    pcm.write_bytes(b"\x02" * 64)
    with pytest.raises(ValueError, match="1 to 20 turns"):
        ScriptInput.from_wire([{"pcm_path": str(pcm)}] * 21)


def test_from_wire_refuses_a_turn_over_one_minute(tmp_path: Path) -> None:
    pcm = tmp_path / "long.pcm"
    pcm.write_bytes(bytes(16_000 * 2 * 61))
    with pytest.raises(ValueError, match="one minute"):
        ScriptInput.from_wire([{"pcm_path": str(pcm)}])


async def test_mic_yields_each_captured_block() -> None:
    mic = _mic([b"a\x00", b"b\x00"])
    assert [pcm async for pcm in mic.chunks()] == [b"a\x00", b"b\x00"]


class _FakeMicSource:
    """A MicAudioSource double that yields a fixed run of 20 ms chunks."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def chunks(self) -> AsyncIterator[AudioChunk]:
        for pcm in self._chunks:
            yield AudioChunk(pcm=pcm, duration_s=0.02)


_FRAME = bytes(640)  # 20 ms of 16 kHz mono S16LE silence
_VOICE = b"\x10\x00" * 320  # 20 ms at a constant non-zero level
_THREE_SECONDS = 150


def _mic(chunks: list[bytes]) -> MicInput:
    return MicInput(cast("MicAudioSource", _FakeMicSource(chunks)))


async def test_mic_silent_for_three_seconds_fails_naming_the_remedies(
    caplog: pytest.LogCaptureFixture,
) -> None:
    mic = _mic([_FRAME] * (_THREE_SECONDS + 10))
    with (
        caplog.at_level(logging.WARNING, logger="punt_vox.voxd.voice_call.inputs"),
        pytest.raises(ValueError, match="no microphone signal") as failure,
    ):
        async for _ in mic.chunks():
            pass
    message = str(failure.value)
    assert "System Settings" in message
    assert "Privacy & Security" in message
    assert "Microphone" in message
    assert "terminal" in message
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "no microphone signal" in warnings[0].message


async def test_mic_with_signal_in_the_first_three_seconds_is_not_failed() -> None:
    mic = _mic([_FRAME] * 100 + [_VOICE] + [_FRAME] * (_THREE_SECONDS * 2))
    forwarded = [pcm async for pcm in mic.chunks()]
    assert len(forwarded) == 101 + _THREE_SECONDS * 2


async def test_mic_forwards_frames_before_the_silence_deadline() -> None:
    mic = _mic([_FRAME] * (_THREE_SECONDS + 1))
    forwarded = 0
    with pytest.raises(ValueError):
        async for _ in mic.chunks():
            forwarded += 1
    assert forwarded == _THREE_SECONDS - 1


async def test_mic_logs_the_capture_level_of_the_first_second(
    caplog: pytest.LogCaptureFixture,
) -> None:
    loud = b"\x00\x40" * 320  # every sample 16384
    mic = _mic([loud] * 50 + [_FRAME] * 10)
    with caplog.at_level(logging.INFO, logger="punt_vox.voxd.voice_call.inputs"):
        async for _ in mic.chunks():
            pass
    levels = [r for r in caplog.records if "capture level" in r.message]
    assert len(levels) == 1
    assert "peak 16384" in levels[0].message
    assert "rms 16384" in levels[0].message


async def test_script_input_never_trips_the_silence_check() -> None:
    script = ScriptInput((ScriptTurn(bytes(640), 0.0),))
    stream = script.chunks()
    for _ in range(_THREE_SECONDS + 5):
        assert await anext(stream) == bytes(640)
