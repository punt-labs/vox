"""Tests for :mod:`punt_vox.voxd.voice_call.capture_probe`."""

from __future__ import annotations

import logging

import pytest

from punt_vox.voxd.conversation_mode.audio_chunk import AudioChunk
from punt_vox.voxd.voice_call.capture_probe import CaptureProbe

_LOGGER = "punt_vox.voxd.voice_call.capture_probe"
_FRAME_S = 0.02
_SILENCE = bytes(640)  # 20 ms of 16 kHz mono S16LE zeros
_VOICE = b"\x10\x00" * 320  # 20 ms at sample value 16
_LIMIT_FRAMES = 150  # 3 s of 20 ms frames


def _feed(probe: CaptureProbe, frames: list[bytes]) -> int:
    """Observe each frame; return how many were accepted before a raise."""
    accepted = 0
    for pcm in frames:
        probe.observe(pcm, _FRAME_S)
        accepted += 1
    return accepted


def _levels(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.message for r in caplog.records if "capture level" in r.message]


def test_silence_for_the_whole_limit_raises_naming_the_remedies() -> None:
    probe = CaptureProbe()
    with pytest.raises(ValueError, match="no microphone signal") as failure:
        _feed(probe, [_SILENCE] * _LIMIT_FRAMES)
    message = str(failure.value)
    assert "3 seconds" in message
    assert "System Settings > Privacy & Security > Microphone" in message
    assert "terminal" in message


def test_the_raise_lands_on_the_frame_that_completes_the_limit() -> None:
    probe = CaptureProbe()
    _feed(probe, [_SILENCE] * (_LIMIT_FRAMES - 1))
    with pytest.raises(ValueError):
        probe.observe(_SILENCE, _FRAME_S)


def test_silence_short_of_the_limit_does_not_raise() -> None:
    assert _feed(CaptureProbe(), [_SILENCE] * (_LIMIT_FRAMES - 1)) == 149


def test_one_non_zero_sample_inside_the_limit_keeps_the_capture_alive() -> None:
    probe = CaptureProbe()
    late_voice = b"\x00" * 638 + b"\x01\x00"  # last sample of the frame is 1
    frames = [_SILENCE] * 100 + [late_voice] + [_SILENCE] * (_LIMIT_FRAMES * 2)
    assert _feed(probe, frames) == len(frames)


def test_silence_after_the_limit_is_ignored_once_signal_was_heard() -> None:
    probe = CaptureProbe()
    _feed(probe, [_VOICE] * _LIMIT_FRAMES)
    assert _feed(probe, [_SILENCE] * (_LIMIT_FRAMES * 3)) == _LIMIT_FRAMES * 3


def test_the_silence_failure_is_logged_once_as_a_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with (
        caplog.at_level(logging.WARNING, logger=_LOGGER),
        pytest.raises(ValueError),
    ):
        _feed(CaptureProbe(), [_SILENCE] * _LIMIT_FRAMES)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert [r.message[:21] for r in warnings] == ["no microphone signal:"]


def test_the_first_second_level_is_logged_once_with_peak_and_rms(
    caplog: pytest.LogCaptureFixture,
) -> None:
    loud = b"\x00\x40" * 320  # every sample 16384
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        _feed(CaptureProbe(), [loud] * 50 + [_SILENCE] * 10)
    assert _levels(caplog) == [
        "Call capture level over the first second: peak 16384, rms 16384"
    ]


def test_the_level_line_mixes_the_whole_first_second(
    caplog: pytest.LogCaptureFixture,
) -> None:
    half_loud = [b"\x00\x40" * 320] * 25 + [_SILENCE] * 25
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        _feed(CaptureProbe(), half_loud)
    # rms over 8000 samples at 16384 and 8000 at 0: isqrt(16384**2 / 2)
    assert _levels(caplog) == [
        "Call capture level over the first second: peak 16384, rms 11585"
    ]


def test_the_level_line_uses_the_absolute_peak_of_negative_samples(
    caplog: pytest.LogCaptureFixture,
) -> None:
    negative = b"\x00\xc0" * 320  # every sample -16384
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        _feed(CaptureProbe(), [negative] * 50)
    assert _levels(caplog) == [
        "Call capture level over the first second: peak 16384, rms 16384"
    ]


def test_no_level_line_before_a_full_second_of_audio(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        _feed(CaptureProbe(), [_VOICE] * 49)
    assert _levels(caplog) == []


def test_an_odd_trailing_byte_is_dropped_not_counted(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        CaptureProbe().observe(b"\x00\x40\xff", 1.0)
    assert _levels(caplog) == [
        "Call capture level over the first second: peak 16384, rms 16384"
    ]


async def test_watch_forwards_pcm_and_fails_a_silent_stream() -> None:
    chunks = [AudioChunk(pcm=_VOICE, duration_s=_FRAME_S)] + [
        AudioChunk(pcm=_SILENCE, duration_s=_FRAME_S)
    ] * 3
    forwarded = [
        pcm async for pcm in CaptureProbe().watch(AudioChunk.as_async_iter(chunks))
    ]
    assert forwarded == [_VOICE, _SILENCE, _SILENCE, _SILENCE]

    silent = [AudioChunk(pcm=_SILENCE, duration_s=1.0)] * 3
    seen = 0
    with pytest.raises(ValueError, match="no microphone signal"):
        async for _ in CaptureProbe().watch(AudioChunk.as_async_iter(silent)):
            seen += 1
    assert seen == 2
