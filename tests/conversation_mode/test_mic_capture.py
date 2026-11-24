"""Tests for :mod:`punt_vox.voxd.conversation_mode.mic_capture`."""

from __future__ import annotations

import asyncio

import pytest

from conversation_mode._mic_fakes import FakeBackend, FakeInputStream
from punt_vox.voxd.conversation_mode.mic_capture import MicCapture


async def test_open_holds_the_device_before_any_chunk_is_read() -> None:
    created: list[FakeInputStream] = []
    capture = MicCapture.open(FakeBackend(created), sample_rate_hz=16_000, chunk_s=0.02)
    (stream,) = created
    assert not stream.closed
    assert stream.kwargs["blocksize"] == 320
    capture.close()
    assert stream.closed


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
