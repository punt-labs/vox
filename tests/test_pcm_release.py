"""StreamRelease: stop or abort, always close, and a cancel outranks a failure."""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Self, final

import pytest

from punt_vox.voxd.pcm_output import DeviceCall
from punt_vox.voxd.pcm_release import StreamRelease


@final
class Stream:
    """An output stream that records how it was released."""

    __slots__ = ("calls", "gate", "stop_error")

    calls: list[str]
    gate: threading.Event | None
    stop_error: Exception | None

    def __new__(cls) -> Self:
        self = super().__new__(cls)
        self.calls = []
        self.gate = None
        self.stop_error = None
        return self

    def start(self) -> None:
        self.calls.append("start")

    def stop(self) -> None:
        if self.gate is not None:
            self.gate.wait(timeout=5)
        self.calls.append("stop")
        if self.stop_error is not None:
            raise self.stop_error

    def abort(self) -> None:
        self.calls.append("abort")

    def close(self) -> None:
        self.calls.append("close")


class TestRun:
    async def test_a_drained_stream_is_stopped_then_closed(self) -> None:
        stream = Stream()
        await StreamRelease.run(stream, drain=True)
        assert stream.calls == ["stop", "close"]

    async def test_any_other_stream_is_aborted_then_closed(self) -> None:
        stream = Stream()
        await StreamRelease.run(stream, drain=False)
        assert stream.calls == ["abort", "close"]

    async def test_a_failed_stop_still_closes_the_device_and_raises(self) -> None:
        stream = Stream()
        stream.stop_error = OSError("device unplugged")
        with pytest.raises(OSError, match="unplugged"):
            await StreamRelease.run(stream, drain=True)
        assert stream.calls == ["stop", "close"]

    async def test_a_cancelled_release_finishes_then_raises_the_cancellation(
        self,
    ) -> None:
        """The device call is never abandoned half-way: the mutex is still held."""
        stream = Stream()
        stream.gate = threading.Event()
        releasing = asyncio.create_task(StreamRelease.run(stream, drain=True))
        await asyncio.sleep(0.01)
        releasing.cancel()
        stream.gate.set()
        with pytest.raises(asyncio.CancelledError):
            await releasing
        assert stream.calls == ["stop", "close"]

    async def test_a_failure_during_a_cancelled_release_is_logged_not_raised(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        stream = Stream()
        stream.gate = threading.Event()
        stream.stop_error = OSError("device unplugged")
        releasing = asyncio.create_task(StreamRelease.run(stream, drain=True))
        await asyncio.sleep(0.01)
        releasing.cancel()
        stream.gate.set()
        with pytest.raises(asyncio.CancelledError):
            await releasing
        assert releasing.cancelled()
        assert "during a cancelled close" in caplog.text
        assert "device unplugged" in caplog.text


class TestAfterCancelledOpen:
    async def test_a_started_stream_is_aborted_and_closed(self) -> None:
        stream = Stream()
        starting = await DeviceCall.run(lambda: stream)
        await StreamRelease.after_cancelled_open(starting)
        assert stream.calls == ["abort", "close"]

    async def test_a_failed_start_is_logged_and_nothing_is_released(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        def failing() -> Stream:
            msg = "device busy"
            raise RuntimeError(msg)

        starting = await DeviceCall.run(failing)
        with caplog.at_level(logging.ERROR, logger="punt_vox.voxd.pcm_release"):
            await StreamRelease.after_cancelled_open(starting)
        assert "after a cancelled open" in caplog.text
        assert "device busy" in caplog.text
