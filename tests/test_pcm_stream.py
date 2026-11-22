"""Streaming PCM sink: feed, flush, drain, ownership, and race interleavings.

The race tests run the device side on a real thread, the way PortAudio
invokes the callback, against feeds and flushes from the event loop, and
check invariants that hold under every interleaving rather than one order.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Self, final

import pytest

from punt_vox.voxd.pcm_stream import (
    PCM_SAMPLE_RATE_HZ,
    OutputCallback,
    PcmBuffer,
    PcmDuration,
    PcmStreamSink,
)
from punt_vox.voxd.playback import PlaybackQueue

BLOCK = 320
OLD = b"\x11\x11"
NEW = b"\x22\x22"


@final
class FakeOutputStream:
    """An output device whose callback the test pulls, block by block."""

    __slots__ = (
        "_callback",
        "aborted",
        "closed",
        "heard",
        "start_error",
        "started",
        "stopped",
    )

    _callback: OutputCallback
    aborted: bool
    closed: bool
    heard: bytearray
    start_error: Exception | None
    started: bool
    stopped: bool

    def __new__(cls, callback: OutputCallback) -> Self:
        self = super().__new__(cls)
        self._callback = callback
        self.aborted = False
        self.closed = False
        self.started = False
        self.stopped = False
        self.start_error = None
        self.heard = bytearray()
        return self

    def pull(self, blocks: int = 1) -> None:
        for _ in range(blocks):
            out = bytearray(BLOCK * 2)
            self._callback(out, BLOCK, None, None)
            self.heard += out

    def start(self) -> None:
        if self.start_error is not None:
            raise self.start_error
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def abort(self) -> None:
        self.aborted = True

    def close(self) -> None:
        self.closed = True


@final
class FakeDevice:
    """Factory that records the one stream a sink opens."""

    __slots__ = ("kwargs", "stream")

    kwargs: dict[str, object]
    stream: FakeOutputStream | None

    def __new__(cls) -> Self:
        self = super().__new__(cls)
        self.kwargs = {}
        self.stream = None
        return self

    def __call__(
        self, *, samplerate: int, blocksize: int, callback: OutputCallback
    ) -> FakeOutputStream:
        self.kwargs = {"samplerate": samplerate, "blocksize": blocksize}
        self.stream = FakeOutputStream(callback)
        return self.stream

    @property
    def opened(self) -> FakeOutputStream:
        assert self.stream is not None, "no stream was opened"
        return self.stream


def ms_of(pattern: bytes, ms: int) -> bytes:
    return pattern * (PCM_SAMPLE_RATE_HZ * ms // 1000)


def open_sink(loop: asyncio.AbstractEventLoop) -> tuple[PcmStreamSink, FakeDevice]:
    device = FakeDevice()
    sink = PcmStreamSink(loop)
    sink.open(device)
    return sink, device


class TestPcmBuffer:
    def test_pull_returns_whole_frames_in_order(self) -> None:
        buffer = PcmBuffer()
        buffer.append(b"\x01\x02\x03")
        assert buffer.pull(4) == b"\x01\x02"
        assert len(buffer) == 1

    def test_clear_reports_dropped_bytes(self) -> None:
        buffer = PcmBuffer()
        buffer.append(b"\x00" * 10)
        assert buffer.clear() == 10
        assert buffer.clear() == 0


class TestPcmDuration:
    def test_one_second_of_pcm_16000(self) -> None:
        assert PcmDuration(32_000).ms == pytest.approx(1000.0)


class TestPcmStreamSink:
    async def test_open_starts_a_16k_stream_with_20ms_blocks(self) -> None:
        _, device = open_sink(asyncio.get_running_loop())
        assert device.kwargs == {"samplerate": 16_000, "blocksize": BLOCK}
        assert device.opened.started

    async def test_fed_audio_plays_in_order_then_silence(self) -> None:
        sink, device = open_sink(asyncio.get_running_loop())
        sink.feed(ms_of(OLD, 10))
        sink.feed(ms_of(NEW, 10))
        device.opened.pull(2)
        heard = bytes(device.opened.heard)
        assert heard == ms_of(OLD, 10) + ms_of(NEW, 10) + bytes(BLOCK * 2)

    async def test_flush_silences_the_next_block(self) -> None:
        sink, device = open_sink(asyncio.get_running_loop())
        sink.feed(ms_of(OLD, 500))
        device.opened.pull(5)
        report = sink.flush()
        device.opened.pull(1)
        assert report.dropped.ms == pytest.approx(400.0)
        assert device.opened.heard[-BLOCK * 2 :] == bytes(BLOCK * 2)

    async def test_feed_after_flush_is_ignored(self) -> None:
        sink, device = open_sink(asyncio.get_running_loop())
        sink.feed(ms_of(OLD, 100))
        sink.flush()
        sink.feed(ms_of(OLD, 100))
        device.opened.pull(5)
        assert not any(device.opened.heard)

    async def test_second_flush_drops_nothing(self) -> None:
        sink, _ = open_sink(asyncio.get_running_loop())
        sink.feed(ms_of(OLD, 100))
        sink.flush()
        assert sink.flush().dropped.nbytes == 0

    async def test_wait_returns_after_the_last_block_plays(self) -> None:
        sink, device = open_sink(asyncio.get_running_loop())
        sink.feed(ms_of(OLD, 30))
        sink.finish()
        device.opened.pull(1)
        await asyncio.sleep(0)
        assert not sink._done.is_set()  # pyright: ignore[reportPrivateUsage]
        device.opened.pull(1)
        await asyncio.wait_for(sink.wait(), timeout=1)

    async def test_finish_with_nothing_pending_completes_at_once(self) -> None:
        sink, _ = open_sink(asyncio.get_running_loop())
        sink.finish()
        await asyncio.wait_for(sink.wait(), timeout=1)

    async def test_flush_releases_a_waiter(self) -> None:
        sink, _ = open_sink(asyncio.get_running_loop())
        sink.feed(ms_of(OLD, 1000))
        waiter = asyncio.create_task(sink.wait())
        await asyncio.sleep(0)
        sink.flush()
        await asyncio.wait_for(waiter, timeout=1)

    async def test_close_aborts_and_reports(self) -> None:
        sink, device = open_sink(asyncio.get_running_loop())
        sink.feed(ms_of(OLD, 100))
        device.opened.pull(2)
        report = sink.close()
        assert device.opened.aborted
        assert device.opened.closed
        assert report.fed.ms == pytest.approx(100.0)
        assert report.played.ms == pytest.approx(40.0)
        assert report.dropped.ms == pytest.approx(60.0)
        assert not report.interrupted

    async def test_close_after_play_out_drains_the_last_block(self) -> None:
        sink, device = open_sink(asyncio.get_running_loop())
        sink.feed(ms_of(OLD, 30))
        sink.finish()
        device.opened.pull(2)
        await asyncio.wait_for(sink.wait(), timeout=1)
        sink.close()
        assert device.opened.stopped
        assert not device.opened.aborted

    async def test_close_after_the_last_full_block_drains_it(self) -> None:
        sink, device = open_sink(asyncio.get_running_loop())
        sink.feed(ms_of(OLD, 20))
        device.opened.pull(1)
        sink.finish()
        await asyncio.wait_for(sink.wait(), timeout=1)
        sink.close()
        assert device.opened.stopped
        assert not device.opened.aborted

    async def test_close_report_counts_audio_the_flush_dropped(self) -> None:
        sink, device = open_sink(asyncio.get_running_loop())
        sink.feed(ms_of(OLD, 100))
        device.opened.pull(1)
        sink.flush()
        report = sink.close()
        assert device.opened.aborted
        assert report.dropped.ms == pytest.approx(80.0)
        assert report.interrupted

    async def test_failed_device_start_closes_the_device(self) -> None:
        device = FakeDevice()
        sink = PcmStreamSink(asyncio.get_running_loop())

        def failing(
            *, samplerate: int, blocksize: int, callback: OutputCallback
        ) -> FakeOutputStream:
            stream = device(
                samplerate=samplerate, blocksize=blocksize, callback=callback
            )
            stream.start_error = RuntimeError("device busy")
            return stream

        with pytest.raises(RuntimeError, match="device busy"):
            sink.open(failing)
        assert device.opened.closed

    async def test_underflow_counts_a_starved_partial_block(self) -> None:
        sink, device = open_sink(asyncio.get_running_loop())
        sink.feed(ms_of(OLD, 30))
        device.opened.pull(3)
        assert sink.close().underflows == 1


@final
class DeviceThread:
    """Pull blocks on a real thread, as PortAudio does, until stopped."""

    __slots__ = ("_stop", "_stream", "_thread")

    _stop: threading.Event
    _stream: FakeOutputStream
    _thread: threading.Thread

    def __new__(cls, stream: FakeOutputStream) -> Self:
        self = super().__new__(cls)
        self._stream = stream
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run)
        return self

    def __enter__(self) -> Self:
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        self._thread.join()

    def _run(self) -> None:
        while not self._stop.is_set():
            self._stream.pull(1)


def flush_dropped(
    sink: PcmStreamSink, barrier: threading.Barrier, out: list[int]
) -> None:
    barrier.wait()
    out.append(sink.flush().dropped.nbytes)


class TestPcmStreamRaces:
    """Device thread vs. loop: invariants that hold under any interleaving."""

    ROUNDS = 200

    async def test_nothing_fed_before_a_flush_plays_after_it(self) -> None:
        """A clear racing the device's pulls is exact: old audio never resumes."""
        for _ in range(self.ROUNDS):
            sink, device = open_sink(asyncio.get_running_loop())
            sink.feed(ms_of(OLD, 200))
            with DeviceThread(device.opened):
                await asyncio.sleep(0.001)
                sink.flush()
                heard_at_flush = len(device.opened.heard)
                await asyncio.sleep(0.001)
            # The device may be mid-pull when the flush lands: one block, no more.
            after = bytes(device.opened.heard[heard_at_flush + BLOCK * 2 :])
            assert not any(after), "pre-flush audio played after the flush"

    async def test_bytes_are_conserved_across_feed_pull_flush(self) -> None:
        """Every fed byte is either played or dropped, never both or neither."""
        for _ in range(self.ROUNDS):
            sink, device = open_sink(asyncio.get_running_loop())
            with DeviceThread(device.opened):
                for _ in range(20):
                    sink.feed(ms_of(OLD, 10))
                sink.flush()
            report = sink.close()
            assert report.played.nbytes + report.dropped.nbytes == report.fed.nbytes

    async def test_concurrent_flushes_drop_each_byte_once(self) -> None:
        """Stop during a stop: two flushers on two threads never double-count."""
        for _ in range(self.ROUNDS):
            sink, _ = open_sink(asyncio.get_running_loop())
            sink.feed(ms_of(OLD, 200))
            barrier = threading.Barrier(2)
            dropped: list[int] = []
            threads = [
                threading.Thread(target=flush_dropped, args=(sink, barrier, dropped))
                for _ in range(2)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            assert sorted(dropped) == [0, len(ms_of(OLD, 200))]

    async def test_flush_racing_natural_end_always_releases_wait(self) -> None:
        """Stop vs. turn end: whichever wins, the waiter returns."""
        for _ in range(self.ROUNDS):
            sink, device = open_sink(asyncio.get_running_loop())
            sink.feed(ms_of(OLD, 20))
            sink.finish()
            thread = threading.Thread(target=device.opened.pull, args=(2,))
            thread.start()
            sink.flush()
            thread.join()
            await asyncio.wait_for(sink.wait(), timeout=1)
            assert sink.close().interrupted


class TestPlaybackQueueOwnership:
    async def test_stream_holds_the_playback_mutex(self) -> None:
        device = FakeDevice()
        playback = PlaybackQueue(device)
        async with playback.stream_pcm() as sink:
            assert playback.mutex.locked()
            sink.feed(ms_of(OLD, 20))
        assert not playback.mutex.locked()
        assert device.opened.aborted

    async def test_queued_playback_waits_behind_the_stream(self) -> None:
        playback = PlaybackQueue(FakeDevice())
        order: list[str] = []

        async def chime() -> None:
            async with playback.mutex:
                order.append("chime")

        async with playback.stream_pcm() as sink:
            task = asyncio.create_task(chime())
            await asyncio.sleep(0.01)
            order.append("stream")
            sink.flush()
        await task
        assert order == ["stream", "chime"]

    async def test_stream_waits_for_playback_in_progress(self) -> None:
        playback = PlaybackQueue(FakeDevice())
        async with playback.mutex:
            opening = asyncio.create_task(_enter_and_exit(playback))
            await asyncio.sleep(0.01)
            assert not opening.done()
        await asyncio.wait_for(opening, timeout=1)

    async def test_device_is_released_when_the_caller_raises(self) -> None:
        device = FakeDevice()
        playback = PlaybackQueue(device)
        with pytest.raises(RuntimeError):
            async with playback.stream_pcm():
                raise RuntimeError("call dropped")
        assert device.opened.closed
        assert not playback.mutex.locked()


async def _enter_and_exit(playback: PlaybackQueue) -> None:
    async with playback.stream_pcm():
        pass
