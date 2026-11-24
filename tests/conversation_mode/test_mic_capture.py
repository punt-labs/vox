"""Tests for :mod:`punt_vox.voxd.conversation_mode.mic_capture`."""

from __future__ import annotations

import asyncio
import logging

import pytest

from conversation_mode._mic_fakes import FakeBackend, FakeInputStream
from punt_vox.voxd.conversation_mode.input_device import InputDevice
from punt_vox.voxd.conversation_mode.mic_capture import MicCapture


async def test_open_holds_the_device_before_any_chunk_is_read() -> None:
    created: list[FakeInputStream] = []
    capture = MicCapture.open(FakeBackend(created), sample_rate_hz=16_000, chunk_s=0.02)
    (stream,) = created
    assert not stream.closed
    assert stream.kwargs["blocksize"] == 320
    capture.close()
    assert stream.closed


async def test_open_logs_a_device_name_with_a_newline_on_one_line(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The device names itself; its text cannot forge a call's end line."""

    class _ForgingBackend:
        def device(self) -> InputDevice:
            return InputDevice("USB\nCall ended: forged", 48_000.0)

        def open(self, **kwargs: object) -> FakeInputStream:
            return FakeInputStream(**kwargs)

    with caplog.at_level(logging.INFO):
        capture = MicCapture.open(
            _ForgingBackend(), sample_rate_hz=16_000, chunk_s=0.02
        )
    capture.close()
    (line,) = [r.message for r in caplog.records if "input device" in r.message]
    assert line == (
        "Call input device: USB Call ended: forged (default 48000 Hz), "
        "capturing at 16000 Hz"
    )


async def test_open_propagates_a_device_refusal_and_holds_nothing() -> None:
    created: list[FakeInputStream] = []

    def refuse(**_kwargs: object) -> FakeInputStream:
        raise OSError("cannot open input device")

    with pytest.raises(OSError, match="cannot open input device"):
        MicCapture.open(
            FakeBackend(created, open_stream=refuse),
            sample_rate_hz=16_000,
            chunk_s=0.02,
        )
    assert created == []


async def test_chunks_yield_fed_pcm_with_the_configured_duration() -> None:
    created: list[FakeInputStream] = []
    capture = MicCapture.open(FakeBackend(created), sample_rate_hz=16_000, chunk_s=0.05)
    gen = capture.chunks()
    task = asyncio.ensure_future(gen.__anext__())
    await asyncio.sleep(0)
    created[0].feed(b"\x01\x00")
    chunk = await task
    assert chunk.pcm == b"\x01\x00"
    assert chunk.duration_s == pytest.approx(0.05)
    await gen.aclose()
    capture.close()


async def test_blocks_captured_before_the_reader_starts_are_discarded() -> None:
    """The device is held from open(); what it heard before pickup is not sent."""
    created: list[FakeInputStream] = []
    capture = MicCapture.open(FakeBackend(created), sample_rate_hz=16_000, chunk_s=0.02)
    created[0].feed(b"stale")
    await asyncio.sleep(0)  # the callback's put reaches the loop
    gen = capture.chunks()
    task = asyncio.ensure_future(gen.__anext__())
    await asyncio.sleep(0)
    assert not task.done()
    created[0].feed(b"fresh")
    assert (await task).pcm == b"fresh"
    await gen.aclose()
    capture.close()


async def test_a_reader_that_falls_behind_sees_only_the_newest_second(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """At 20 ms blocks the queue holds one second: 50 blocks, oldest dropped."""
    created: list[FakeInputStream] = []
    capture = MicCapture.open(FakeBackend(created), sample_rate_hz=16_000, chunk_s=0.02)
    gen = capture.chunks()
    task = asyncio.ensure_future(gen.__anext__())
    await asyncio.sleep(0)
    created[0].feed(b"first")
    assert (await task).pcm == b"first"
    for index in range(60):
        created[0].feed(str(index).encode())
    with caplog.at_level(logging.WARNING):
        chunk = await gen.__anext__()
    assert chunk.pcm == b"10"
    lines = [r.message for r in caplog.records if "dropped" in r.message]
    assert lines == ["mic capture: dropped 10 blocks the reader had not taken"]
    await gen.aclose()
    capture.close()
