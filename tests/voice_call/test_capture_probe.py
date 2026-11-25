"""Tests for :mod:`punt_vox.voxd.voice_call.capture_probe`."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import pytest

from punt_vox.voxd.conversation_mode.audio_chunk import AudioChunk
from punt_vox.voxd.voice_call.capture_probe import CaptureProbe

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

_LOGGER = "punt_vox.voxd.voice_call.capture_probe"
_LEVEL_LOGGER = "punt_vox.voxd.voice_call.capture_level"
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
    with caplog.at_level(logging.INFO, logger=_LEVEL_LOGGER):
        _feed(CaptureProbe(), [loud] * 50 + [_SILENCE] * 10)
    assert _levels(caplog) == [
        "Call capture level over the first second: peak 16384, rms 16384"
    ]


def test_the_level_line_mixes_the_whole_first_second(
    caplog: pytest.LogCaptureFixture,
) -> None:
    half_loud = [b"\x00\x40" * 320] * 25 + [_SILENCE] * 25
    with caplog.at_level(logging.INFO, logger=_LEVEL_LOGGER):
        _feed(CaptureProbe(), half_loud)
    # rms over 8000 samples at 16384 and 8000 at 0: isqrt(16384**2 / 2)
    assert _levels(caplog) == [
        "Call capture level over the first second: peak 16384, rms 11585"
    ]


def test_the_level_line_uses_the_absolute_peak_of_negative_samples(
    caplog: pytest.LogCaptureFixture,
) -> None:
    negative = b"\x00\xc0" * 320  # every sample -16384
    with caplog.at_level(logging.INFO, logger=_LEVEL_LOGGER):
        _feed(CaptureProbe(), [negative] * 50)
    assert _levels(caplog) == [
        "Call capture level over the first second: peak 16384, rms 16384"
    ]


def test_no_level_line_before_a_full_second_of_audio(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger=_LEVEL_LOGGER):
        _feed(CaptureProbe(), [_VOICE] * 49)
    assert _levels(caplog) == []


def test_an_odd_trailing_byte_is_dropped_not_counted(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger=_LEVEL_LOGGER):
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


async def test_a_stream_that_never_delivers_fails_within_the_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("punt_vox.voxd.voice_call.capture_probe._SILENCE_LIMIT_S", 0.05)

    async def never() -> AsyncGenerator[AudioChunk]:
        await asyncio.sleep(3600)
        yield AudioChunk(pcm=_VOICE, duration_s=_FRAME_S)

    with pytest.raises(ValueError, match="no audio for") as failure:
        async with asyncio.timeout(2):
            async for _ in CaptureProbe().watch(never()):
                pass
    assert "System Settings > Privacy & Security > Microphone" in str(failure.value)


async def test_sparse_silent_blocks_fail_within_the_wall_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One silent block well inside each per-block wait still fails on time.

    Scaled from a 3 s limit: a 20 ms zero block every 2.5 s never trips the
    per-block wait, and its nominal durations need minutes to reach the
    silence limit. The wall budget from the first wait fails it instead, with
    the silence message, not the no-audio one.
    """
    limit = 0.3
    monkeypatch.setattr(
        "punt_vox.voxd.voice_call.capture_probe._SILENCE_LIMIT_S", limit
    )

    async def sparse() -> AsyncGenerator[AudioChunk]:
        while True:
            await asyncio.sleep(limit * 2.5 / 3.0)
            yield AudioChunk(pcm=_SILENCE, duration_s=_FRAME_S * limit / 3.0)

    started = asyncio.get_running_loop().time()
    with pytest.raises(ValueError, match="delivered only silence") as failure:
        async with asyncio.timeout(2):
            async for _ in CaptureProbe().watch(sparse()):
                pass
    assert asyncio.get_running_loop().time() - started < 1.0
    assert "System Settings > Privacy & Security > Microphone" in str(failure.value)


async def test_a_silence_failure_closes_the_source_before_it_reaches_the_caller() -> (
    None
):
    """The device is released by the probe, not later by garbage collection."""
    closed_at: list[bool] = []

    async def dead() -> AsyncGenerator[AudioChunk]:
        try:
            while True:
                yield AudioChunk(pcm=_SILENCE, duration_s=1.0)
        finally:
            closed_at.append(True)

    with pytest.raises(ValueError, match="no microphone signal"):
        async for _ in CaptureProbe().watch(dead()):
            pass
    assert closed_at == [True]


async def test_time_the_consumer_spends_away_does_not_count_against_the_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The wall budget measures waiting for audio, not the consumer's absence.

    A live microphone delivers an initial silent block promptly; the consumer
    then spends longer than the limit away (a slow socket send) before pulling
    again. The next pull must not time out: the mic has more audio ready, and
    the voiced block that follows proves the capture is alive.
    """
    limit = 0.2
    monkeypatch.setattr(
        "punt_vox.voxd.voice_call.capture_probe._SILENCE_LIMIT_S", limit
    )

    async def prompt() -> AsyncGenerator[AudioChunk]:
        yield AudioChunk(pcm=_SILENCE, duration_s=_FRAME_S)
        yield AudioChunk(pcm=_VOICE, duration_s=_FRAME_S)

    forwarded: list[bytes] = []
    async with asyncio.timeout(5):
        async for pcm in CaptureProbe().watch(prompt()):
            forwarded.append(pcm)
            if pcm == _SILENCE:
                await asyncio.sleep(limit * 1.5)  # the consumer is away
    assert forwarded == [_SILENCE, _VOICE]
