"""The silent output device drives a real PCM sink without a sound card."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from punt_vox.voxd.pcm_audio import PCM_SAMPLE_RATE_HZ
from punt_vox.voxd.pcm_output import OutputDeviceLostError, sounddevice_output_stream
from punt_vox.voxd.pcm_silent import SilentOutputStream
from punt_vox.voxd.pcm_stream import PcmStreamSink
from punt_vox.voxd.playback import PlaybackQueue

if TYPE_CHECKING:
    from collections.abc import Buffer, Callable

    from punt_vox.voxd.pcm_output import OutputCallback


def _ms(ms: int) -> bytes:
    return b"\x01\x00" * (PCM_SAMPLE_RATE_HZ * ms // 1000)


async def test_fed_audio_plays_out_in_real_time() -> None:
    sink = PcmStreamSink(asyncio.get_running_loop())
    await sink.open(SilentOutputStream)
    sink.feed(_ms(100))
    sink.finish()
    async with asyncio.timeout(2):
        await sink.wait()
    report = await sink.close()
    assert report.played.ms == 100
    assert report.dropped.ms == 0
    assert not report.interrupted
    assert not report.device_lost


async def test_a_flush_drops_what_has_not_played_yet() -> None:
    sink = PcmStreamSink(asyncio.get_running_loop())
    await sink.open(SilentOutputStream)
    sink.feed(_ms(2000))
    await asyncio.sleep(0.1)
    sink.flush()
    report = await sink.close()
    assert report.interrupted
    assert 0 < report.played.ms < 1000
    assert report.dropped.ms + report.played.ms == 2000


async def test_closing_an_idle_sink_stops_the_device_thread() -> None:
    sink = PcmStreamSink(asyncio.get_running_loop())
    await sink.open(SilentOutputStream)
    report = await sink.close()
    assert report.played.ms == 0
    assert not report.device_lost


def test_env_var_silent_selects_the_device_with_a_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("VOX_PCM_OUTPUT", "silent")
    with caplog.at_level("WARNING", logger="punt_vox.voxd.pcm_silent"):
        factory = SilentOutputStream.from_env(sounddevice_output_stream)
    assert factory is SilentOutputStream
    assert "VOX_PCM_OUTPUT=silent" in caplog.text


def test_env_var_unset_keeps_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VOX_PCM_OUTPUT", raising=False)
    factory = SilentOutputStream.from_env(sounddevice_output_stream)
    assert factory is sounddevice_output_stream


def test_the_playback_queue_reads_the_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOX_PCM_OUTPUT", "silent")
    assert PlaybackQueue()._output_stream is SilentOutputStream


def _crashing_device(
    *,
    samplerate: int,
    blocksize: int,
    callback: OutputCallback,
    finished_callback: Callable[[], None],
) -> SilentOutputStream:
    """Open the silent device on a callback that raises on its first block."""
    del callback

    def boom(_out: Buffer, _frames: int, _time: object, _status: object) -> None:
        msg = "the sink's callback crashed"
        raise ValueError(msg)

    return SilentOutputStream(
        samplerate=samplerate,
        blocksize=blocksize,
        callback=boom,
        finished_callback=finished_callback,
    )


async def test_a_crashing_callback_stops_the_device_and_fails_the_wait(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A callback error ends the stream as PortAudio's would: wait() raises."""
    sink = PcmStreamSink(asyncio.get_running_loop())
    with caplog.at_level("ERROR", logger="punt_vox.voxd.pcm_silent"):
        await sink.open(_crashing_device)
        with pytest.raises(OutputDeviceLostError):
            async with asyncio.timeout(2):
                await sink.wait()
    report = await sink.close()
    assert report.device_lost
    assert "the sink's callback crashed" in caplog.text
