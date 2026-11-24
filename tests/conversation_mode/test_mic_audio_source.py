"""Tests for :mod:`punt_vox.voxd.conversation_mode.mic_audio_source`.

No real audio hardware, no ``sounddevice`` C extension calls -- every test
drives :class:`MicAudioSource` through
:class:`conversation_mode._mic_fakes.FakeBackend`. Two-step
timing pattern used throughout: schedule the generator's first ``__anext__``
as a task, ``await asyncio.sleep(0)`` once to let it run synchronously up to
its first suspension point (``await queue.get()``), then feed the fake
stream and await the task -- mirroring how a real PortAudio callback thread
delivers audio asynchronously relative to the consuming coroutine.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from conversation_mode._mic_fakes import FAKE_DEVICE, FakeBackend, FakeInputStream
from punt_vox.voxd.conversation_mode.audio_chunk import SAMPLE_RATE_HZ
from punt_vox.voxd.conversation_mode.input_device import InputDevice, PortAudio
from punt_vox.voxd.conversation_mode.mic_audio_source import MicAudioSource

if TYPE_CHECKING:
    from collections.abc import Callable

    from sounddevice import DeviceInfo


async def _advance_to_first_await() -> None:
    await asyncio.sleep(0)


def _source(
    created: list[FakeInputStream],
    *,
    sample_rate_hz: int = SAMPLE_RATE_HZ,
    chunk_s: float = 0.02,
) -> MicAudioSource:
    return MicAudioSource(
        FakeBackend(created), sample_rate_hz=sample_rate_hz, chunk_s=chunk_s
    )


class TestMicAudioSourceChunks:
    async def test_yields_the_fed_pcm(self) -> None:
        created: list[FakeInputStream] = []
        source = _source(created)
        gen = source.chunks()
        task = asyncio.ensure_future(gen.__anext__())
        await _advance_to_first_await()

        (stream,) = created
        stream.feed(b"\x01\x00\x02\x00")
        chunk = await task
        assert chunk.pcm == b"\x01\x00\x02\x00"

        await gen.aclose()

    async def test_every_chunk_carries_the_configured_duration(self) -> None:
        created: list[FakeInputStream] = []
        source = _source(created, chunk_s=0.05)
        gen = source.chunks()
        task = asyncio.ensure_future(gen.__anext__())
        await _advance_to_first_await()
        created[0].feed(b"\x00\x00")
        chunk = await task
        assert chunk.duration_s == pytest.approx(0.05)
        await gen.aclose()

    async def test_requests_the_configured_sample_rate(self) -> None:
        created: list[FakeInputStream] = []
        source = _source(created, sample_rate_hz=22_050)
        gen = source.chunks()
        task = asyncio.ensure_future(gen.__anext__())
        await _advance_to_first_await()
        created[0].feed(b"\x00\x00")
        await task
        await gen.aclose()

        (stream,) = created
        assert stream.kwargs["samplerate"] == pytest.approx(22_050.0)

    async def test_default_sample_rate_matches_the_shared_constant(self) -> None:
        created: list[FakeInputStream] = []
        source = _source(created)
        gen = source.chunks()
        task = asyncio.ensure_future(gen.__anext__())
        await _advance_to_first_await()
        created[0].feed(b"\x00\x00")
        await task
        await gen.aclose()

        assert created[0].kwargs["samplerate"] == pytest.approx(float(SAMPLE_RATE_HZ))

    async def test_closing_the_generator_releases_the_stream(self) -> None:
        created: list[FakeInputStream] = []
        source = _source(created)
        gen = source.chunks()
        task = asyncio.ensure_future(gen.__anext__())
        await _advance_to_first_await()
        created[0].feed(b"\x00\x00")
        await task

        assert created[0].closed is False
        await gen.aclose()
        assert created[0].closed is True

    async def test_input_overflow_is_logged_not_raised(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        created: list[FakeInputStream] = []
        source = _source(created)
        gen = source.chunks()
        task = asyncio.ensure_future(gen.__anext__())
        await _advance_to_first_await()

        with caplog.at_level(logging.WARNING):
            created[0].feed(b"\x00\x00", input_overflow=True)
            chunk = await task

        assert chunk.pcm == b"\x00\x00"
        assert any("overflow" in record.message for record in caplog.records)
        await gen.aclose()

    async def test_multiple_feeds_yield_multiple_chunks_in_order(self) -> None:
        created: list[FakeInputStream] = []
        source = _source(created)
        gen = source.chunks()
        first_task = asyncio.ensure_future(gen.__anext__())
        await _advance_to_first_await()
        created[0].feed(b"\x01\x00")
        first_chunk = await first_task

        second_task = asyncio.ensure_future(gen.__anext__())
        await _advance_to_first_await()
        created[0].feed(b"\x02\x00")
        second_chunk = await second_task

        assert first_chunk.pcm == b"\x01\x00"
        assert second_chunk.pcm == b"\x02\x00"
        await gen.aclose()


class _FakePortAudioError(Exception):
    pass


def _device_info(name: str, default_samplerate: float) -> Callable[[str], DeviceInfo]:
    def query_devices(kind: str) -> DeviceInfo:
        assert kind == "input"
        return {"name": name, "default_samplerate": default_samplerate}

    return query_devices


def test_the_fake_backend_reports_the_fake_device() -> None:
    assert FakeBackend().device() == FAKE_DEVICE
    assert FakeBackend().device() == InputDevice("Fake Microphone", 48_000.0)


async def test_opening_the_stream_logs_the_device_name_and_rates(
    caplog: pytest.LogCaptureFixture,
) -> None:
    created: list[FakeInputStream] = []
    gen = _source(created, sample_rate_hz=16_000).chunks()
    with caplog.at_level(logging.INFO):
        task = asyncio.ensure_future(gen.__anext__())
        await _advance_to_first_await()
        created[0].feed(b"\x00\x00")
        await task
    await gen.aclose()
    (line,) = [r.message for r in caplog.records if "input device" in r.message]
    assert line == (
        "Call input device: Fake Microphone (default 48000 Hz), capturing at 16000 Hz"
    )


def test_default_device_query_reads_portaudio(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(
        sys.modules,
        "sounddevice",
        SimpleNamespace(
            PortAudioError=_FakePortAudioError,
            query_devices=_device_info("MacBook Air Microphone", 48000.0),
        ),
    )
    assert PortAudio().device() == InputDevice("MacBook Air Microphone", 48000.0)


def test_default_device_query_without_a_device_is_an_oserror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_device(kind: str) -> DeviceInfo:
        raise _FakePortAudioError(f"Error querying device {kind}")

    monkeypatch.setitem(
        sys.modules,
        "sounddevice",
        SimpleNamespace(PortAudioError=_FakePortAudioError, query_devices=no_device),
    )
    with pytest.raises(OSError, match="no default input device"):
        PortAudio().device()


async def test_a_device_open_failure_propagates_out_of_chunks() -> None:
    def refuse(**_kwargs: object) -> FakeInputStream:
        raise OSError("no default input device available")

    source = MicAudioSource(FakeBackend(open_stream=refuse))
    with pytest.raises(OSError, match="no default input device available"):
        await anext(source.chunks())


async def test_default_source_opens_and_closes_a_portaudio_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[FakeInputStream] = []
    monkeypatch.setitem(
        sys.modules,
        "sounddevice",
        SimpleNamespace(
            RawInputStream=FakeBackend(created).open,
            PortAudioError=_FakePortAudioError,
            query_devices=_device_info("Built-in", 44100.0),
        ),
    )
    gen = MicAudioSource(PortAudio()).chunks()
    next_chunk = asyncio.create_task(anext(gen))
    await _advance_to_first_await()
    (stream,) = created
    stream.feed(b"\x01\x00" * 320)
    chunk = await next_chunk
    await gen.aclose()

    assert stream.kwargs["blocksize"] == 320
    assert stream.kwargs["channels"] == 1
    assert stream.kwargs["dtype"] == "int16"
    assert chunk.pcm == b"\x01\x00" * 320
    assert stream.closed
