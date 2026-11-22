"""Streaming PCM sink: feed, flush, drain, ownership, and race interleavings.

The race tests run the device side on a real thread, the way PortAudio
invokes the callback, against feeds and flushes from the event loop, and
check invariants that hold under every interleaving rather than one order.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable
from typing import Self, final

import pytest

from punt_vox.voxd.pcm_output import OutputCallback, OutputDeviceLostError
from punt_vox.voxd.pcm_stream import (
    PCM_SAMPLE_RATE_HZ,
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
        "finished_callback",
        "heard",
        "release_gate",
        "start_error",
        "started",
        "stop_error",
        "stopped",
    )

    _callback: OutputCallback
    release_gate: threading.Event | None
    aborted: bool
    closed: bool
    finished_callback: Callable[[], None]
    heard: bytearray
    start_error: Exception | None
    started: bool
    stop_error: Exception | None
    stopped: bool

    def __new__(
        cls, callback: OutputCallback, finished_callback: Callable[[], None]
    ) -> Self:
        self = super().__new__(cls)
        self._callback = callback
        self.finished_callback = finished_callback
        self.aborted = False
        self.closed = False
        self.started = False
        self.stopped = False
        self.start_error = None
        self.stop_error = None
        self.release_gate = None
        self.heard = bytearray()
        return self

    def pull(self, blocks: int = 1, *, pace_s: float = 0.0) -> None:
        for _ in range(blocks):
            out = bytearray(BLOCK * 2)
            self._callback(out, BLOCK, None, None)
            self.heard += out
            if pace_s:
                time.sleep(pace_s)

    def start(self) -> None:
        if self.start_error is not None:
            raise self.start_error
        self.started = True

    def stop(self) -> None:
        if self.release_gate is not None:
            self.release_gate.wait(timeout=5)
        if self.stop_error is not None:
            raise self.stop_error
        self.stopped = True
        self.finished_callback()

    def abort(self) -> None:
        if self.release_gate is not None:
            self.release_gate.wait(timeout=5)
        self.aborted = True
        self.finished_callback()

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
        self,
        *,
        samplerate: int,
        blocksize: int,
        callback: OutputCallback,
        finished_callback: Callable[[], None],
    ) -> FakeOutputStream:
        self.kwargs = {"samplerate": samplerate, "blocksize": blocksize}
        self.stream = FakeOutputStream(callback, finished_callback)
        return self.stream

    @property
    def opened(self) -> FakeOutputStream:
        assert self.stream is not None, "no stream was opened"
        return self.stream


def ms_of(pattern: bytes, ms: int) -> bytes:
    return pattern * (PCM_SAMPLE_RATE_HZ * ms // 1000)


async def open_sink() -> tuple[PcmStreamSink, FakeDevice]:
    device = FakeDevice()
    sink = PcmStreamSink(asyncio.get_running_loop())
    await sink.open(device)
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
        _, device = await open_sink()
        assert device.kwargs == {"samplerate": 16_000, "blocksize": BLOCK}
        assert device.opened.started

    async def test_fed_audio_plays_in_order_then_silence(self) -> None:
        sink, device = await open_sink()
        sink.feed(ms_of(OLD, 10))
        sink.feed(ms_of(NEW, 10))
        device.opened.pull(2)
        heard = bytes(device.opened.heard)
        assert heard == ms_of(OLD, 10) + ms_of(NEW, 10) + bytes(BLOCK * 2)

    async def test_flush_silences_the_next_block(self) -> None:
        sink, device = await open_sink()
        sink.feed(ms_of(OLD, 500))
        device.opened.pull(5)
        report = sink.flush()
        device.opened.pull(1)
        assert report.dropped.ms == pytest.approx(400.0)
        assert device.opened.heard[-BLOCK * 2 :] == bytes(BLOCK * 2)

    async def test_feed_after_flush_is_ignored(self) -> None:
        sink, device = await open_sink()
        sink.feed(ms_of(OLD, 100))
        sink.flush()
        sink.feed(ms_of(OLD, 100))
        device.opened.pull(5)
        assert not any(device.opened.heard)

    async def test_second_flush_drops_nothing(self) -> None:
        sink, _ = await open_sink()
        sink.feed(ms_of(OLD, 100))
        sink.flush()
        assert sink.flush().dropped.nbytes == 0

    async def test_wait_returns_after_the_last_block_plays(self) -> None:
        sink, device = await open_sink()
        sink.feed(ms_of(OLD, 30))
        sink.finish()
        device.opened.pull(1)
        await asyncio.sleep(0)
        assert not sink._done.is_set()  # pyright: ignore[reportPrivateUsage]
        device.opened.pull(1)
        await asyncio.wait_for(sink.wait(), timeout=1)

    async def test_finish_with_nothing_pending_completes_at_once(self) -> None:
        sink, _ = await open_sink()
        sink.finish()
        await asyncio.wait_for(sink.wait(), timeout=1)

    async def test_flush_releases_a_waiter(self) -> None:
        sink, _ = await open_sink()
        sink.feed(ms_of(OLD, 1000))
        waiter = asyncio.create_task(sink.wait())
        await asyncio.sleep(0)
        sink.flush()
        await asyncio.wait_for(waiter, timeout=1)

    async def test_close_aborts_and_reports(self) -> None:
        sink, device = await open_sink()
        sink.feed(ms_of(OLD, 100))
        device.opened.pull(2)
        report = await sink.close()
        assert device.opened.aborted
        assert device.opened.closed
        assert report.fed.ms == pytest.approx(100.0)
        assert report.played.ms == pytest.approx(40.0)
        assert report.dropped.ms == pytest.approx(60.0)
        assert not report.interrupted

    async def test_close_after_play_out_drains_the_last_block(self) -> None:
        sink, device = await open_sink()
        sink.feed(ms_of(OLD, 30))
        sink.finish()
        device.opened.pull(2)
        await asyncio.wait_for(sink.wait(), timeout=1)
        await sink.close()
        assert device.opened.stopped
        assert not device.opened.aborted

    async def test_close_after_the_last_full_block_drains_it(self) -> None:
        sink, device = await open_sink()
        sink.feed(ms_of(OLD, 20))
        device.opened.pull(1)
        sink.finish()
        await asyncio.wait_for(sink.wait(), timeout=1)
        await sink.close()
        assert device.opened.stopped
        assert not device.opened.aborted

    async def test_close_report_counts_audio_the_flush_dropped(self) -> None:
        sink, device = await open_sink()
        sink.feed(ms_of(OLD, 100))
        device.opened.pull(1)
        sink.flush()
        report = await sink.close()
        assert device.opened.aborted
        assert report.dropped.ms == pytest.approx(80.0)
        assert report.interrupted

    async def test_failed_device_start_closes_the_device(self) -> None:
        device = FakeDevice()
        sink = PcmStreamSink(asyncio.get_running_loop())

        def failing(
            *,
            samplerate: int,
            blocksize: int,
            callback: OutputCallback,
            finished_callback: Callable[[], None],
        ) -> FakeOutputStream:
            stream = device(
                samplerate=samplerate,
                blocksize=blocksize,
                callback=callback,
                finished_callback=finished_callback,
            )
            stream.start_error = RuntimeError("device busy")
            return stream

        with pytest.raises(RuntimeError, match="device busy"):
            await sink.open(failing)
        assert device.opened.closed

    async def test_underflow_counts_starved_blocks_after_audio_starts(self) -> None:
        sink, device = await open_sink()
        device.opened.pull(2)
        sink.feed(ms_of(OLD, 30))
        device.opened.pull(3)
        assert (await sink.close()).underflows == 2

    async def test_device_flags_are_counted_and_reported(self) -> None:
        sink, device = await open_sink()
        out = bytearray(BLOCK * 2)
        device.opened._callback(out, BLOCK, None, "output underflow")  # pyright: ignore[reportPrivateUsage]
        report = await sink.close()
        assert report.device_flags == 1
        assert report.degraded


class TestSinkPhases:
    """Transitions of docs/pcm-stream.tex, named after its properties."""

    async def test_feed_after_finish_is_rejected(self) -> None:
        sink, _ = await open_sink()
        sink.finish()
        with pytest.raises(ValueError, match="finished"):
            sink.feed(ms_of(OLD, 10))

    async def test_feed_after_close_is_rejected(self) -> None:
        """Closed streams never accumulate audio."""
        sink, _ = await open_sink()
        await sink.close()
        with pytest.raises(ValueError, match="closed"):
            sink.feed(ms_of(OLD, 10))

    async def test_feed_after_flush_counts_as_dropped(self) -> None:
        """Conservation: fed = played + dropped + pending, even after a flush."""
        sink, _ = await open_sink()
        sink.flush()
        sink.feed(ms_of(OLD, 100))
        report = await sink.close()
        assert report.fed.nbytes == report.dropped.nbytes == len(ms_of(OLD, 100))

    async def test_flush_after_play_out_is_a_no_op(self) -> None:
        sink, device = await open_sink()
        sink.feed(ms_of(OLD, 20))
        sink.finish()
        device.opened.pull(2)
        await asyncio.wait_for(sink.wait(), timeout=1)
        assert sink.flush().dropped.nbytes == 0
        report = await sink.close()
        assert not report.interrupted
        assert device.opened.stopped

    async def test_flush_after_close_is_a_no_op(self) -> None:
        """Stop during stop: a flush that loses the race to close is harmless."""
        sink, _ = await open_sink()
        await sink.close()
        assert sink.flush().dropped.nbytes == 0

    async def test_finish_drops_a_trailing_half_sample_and_drains(self) -> None:
        sink, device = await open_sink()
        sink.feed(ms_of(OLD, 10) + b"\x01")
        sink.finish()
        device.opened.pull(1)
        await asyncio.wait_for(sink.wait(), timeout=1)
        report = await sink.close()
        assert device.opened.stopped
        assert report.dropped.nbytes == 1

    async def test_phase_follows_the_lifecycle(self) -> None:
        sink, _ = await open_sink()
        phases = [sink.phase]
        sink.finish()
        phases.append(sink.phase)
        await sink.close()
        phases.append(sink.phase)
        assert phases == ["streaming", "finished", "closed"]

    async def test_second_open_is_rejected(self) -> None:
        sink, device = await open_sink()
        with pytest.raises(ValueError, match="cannot open"):
            await sink.open(device)
        await sink.close()

    async def test_finish_after_flush_is_a_no_op(self) -> None:
        sink, _ = await open_sink()
        sink.flush()
        sink.finish()
        assert sink.phase == "interrupted"
        await sink.close()

    async def test_finish_after_close_is_rejected(self) -> None:
        sink, _ = await open_sink()
        await sink.close()
        with pytest.raises(ValueError, match="cannot finish"):
            sink.finish()

    async def test_empty_response_is_played_out_not_interrupted(self) -> None:
        """A stop after an empty response finished is a no-op (FlushNoOp)."""
        sink, device = await open_sink()
        sink.finish()
        assert sink.flush().dropped.nbytes == 0
        report = await sink.close()
        assert not report.interrupted
        assert device.opened.stopped

    async def test_close_without_open_reports_nothing_played(self) -> None:
        sink = PcmStreamSink(asyncio.get_running_loop())
        sink.feed(ms_of(OLD, 10))
        report = await sink.close()
        assert report.dropped.nbytes == report.fed.nbytes

    async def test_second_close_is_rejected(self) -> None:
        sink, _ = await open_sink()
        await sink.close()
        with pytest.raises(ValueError, match="already closed"):
            await sink.close()

    async def test_close_releases_a_waiter(self) -> None:
        sink, _ = await open_sink()
        sink.feed(ms_of(OLD, 1000))
        waiter = asyncio.create_task(sink.wait())
        await asyncio.sleep(0)
        await sink.close()
        await asyncio.wait_for(waiter, timeout=1)

    async def test_close_closes_the_device_when_stop_fails(self) -> None:
        sink, device = await open_sink()
        sink.finish()
        device.opened.stop_error = OSError("device unplugged")
        with pytest.raises(OSError, match="unplugged"):
            await sink.close()
        assert device.opened.closed

    async def test_device_lost_wakes_the_waiter_with_an_error(self) -> None:
        sink, device = await open_sink()
        sink.feed(ms_of(OLD, 1000))
        waiter = asyncio.create_task(sink.wait())
        await asyncio.sleep(0)
        threading.Thread(target=device.opened.finished_callback).start()
        with pytest.raises(OutputDeviceLostError):
            await asyncio.wait_for(waiter, timeout=1)
        report = await sink.close()
        assert report.device_lost


class TestDeviceFailures:
    async def test_cancelled_open_closes_the_device_it_was_starting(self) -> None:
        device = FakeDevice()
        sink = PcmStreamSink(asyncio.get_running_loop())
        release = threading.Event()

        def slow(
            *,
            samplerate: int,
            blocksize: int,
            callback: OutputCallback,
            finished_callback: Callable[[], None],
        ) -> FakeOutputStream:
            release.wait(timeout=5)
            return device(
                samplerate=samplerate,
                blocksize=blocksize,
                callback=callback,
                finished_callback=finished_callback,
            )

        opening = asyncio.create_task(sink.open(slow))
        await asyncio.sleep(0.01)
        opening.cancel()
        await asyncio.sleep(0)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await opening
        assert device.opened.started
        assert device.opened.closed

    async def test_cancelled_close_waits_for_the_device_release(self) -> None:
        sink, device = await open_sink()
        release = threading.Event()
        device.opened.release_gate = release
        sink.feed(ms_of(OLD, 100))
        closing = asyncio.create_task(sink.close())
        await asyncio.sleep(0.01)
        closing.cancel()
        await asyncio.sleep(0.01)
        assert not closing.done(), "close returned before the device was released"
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await closing
        assert device.opened.closed

    async def test_cancelled_close_with_a_failing_stop_still_closes(self) -> None:
        sink, device = await open_sink()
        release = threading.Event()
        device.opened.release_gate = release
        device.opened.stop_error = OSError("device unplugged")
        sink.finish()
        closing = asyncio.create_task(sink.close())
        await asyncio.sleep(0.01)
        closing.cancel()
        await asyncio.sleep(0)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await closing
        assert device.opened.closed

    async def test_cancelled_open_whose_start_fails_is_logged(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        device = FakeDevice()
        sink = PcmStreamSink(asyncio.get_running_loop())
        release = threading.Event()

        def slow_failing(
            *,
            samplerate: int,
            blocksize: int,
            callback: OutputCallback,
            finished_callback: Callable[[], None],
        ) -> FakeOutputStream:
            release.wait(timeout=5)
            stream = device(
                samplerate=samplerate,
                blocksize=blocksize,
                callback=callback,
                finished_callback=finished_callback,
            )
            stream.start_error = RuntimeError("device busy")
            return stream

        opening = asyncio.create_task(sink.open(slow_failing))
        await asyncio.sleep(0.01)
        opening.cancel()
        await asyncio.sleep(0)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await opening
        assert device.opened.closed
        assert "after a cancelled open" in caplog.text

    async def test_feed_after_device_lost_raises(self) -> None:
        sink, device = await open_sink()
        device.opened.finished_callback()
        with pytest.raises(OutputDeviceLostError):
            sink.feed(ms_of(OLD, 10))
        await sink.close()

    async def test_device_stopping_after_play_out_is_not_a_loss(self) -> None:
        sink, device = await open_sink()
        sink.feed(ms_of(OLD, 20))
        sink.finish()
        device.opened.pull(2)
        device.opened.finished_callback()
        await asyncio.wait_for(sink.wait(), timeout=1)
        assert not (await sink.close()).device_lost

    async def test_callback_failure_is_logged(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        sink, device = await open_sink()
        with pytest.raises(TypeError, match="read-only"):
            device.opened._callback(b"read-only", BLOCK, None, None)  # pyright: ignore[reportPrivateUsage]
        assert "PCM output callback failed" in caplog.text
        await sink.close()

    async def test_failed_close_still_reports(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.INFO, logger="punt_vox.voxd.pcm_stream")
        sink, device = await open_sink()
        sink.finish()
        device.opened.stop_error = OSError("device unplugged")
        with pytest.raises(OSError, match="unplugged"):
            await sink.close()
        assert "PCM stream closed" in caplog.text


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
            self._stream.pull(1, pace_s=0.0002)


class TestPcmStreamRaces:
    """Device thread vs. loop: invariants that hold under any interleaving."""

    ROUNDS = 200

    async def test_nothing_fed_before_a_flush_plays_after_it(self) -> None:
        """Flush is exact: old audio never resumes after the flush returns."""
        dropped_any = played_any = False
        for _ in range(self.ROUNDS):
            sink, device = await open_sink()
            sink.feed(ms_of(OLD, 1000))
            with DeviceThread(device.opened):
                await asyncio.sleep(0.001)
                report = sink.flush()
                heard_at_flush = len(device.opened.heard)
                await asyncio.sleep(0.001)
            # The device may be mid-pull when the flush lands: one block, no more.
            after = bytes(device.opened.heard[heard_at_flush + BLOCK * 2 :])
            assert not any(after), "pre-flush audio played after the flush"
            dropped_any |= report.dropped.nbytes > 0
            played_any |= sink.played.nbytes > 0
            await sink.close()
        assert dropped_any, "no round flushed while audio was still queued"
        assert played_any, "no round played audio before the flush"

    async def test_bytes_are_conserved_across_feed_pull_flush(self) -> None:
        """Conservation: every fed byte is played or dropped, exactly once."""
        for _ in range(self.ROUNDS):
            sink, device = await open_sink()
            with DeviceThread(device.opened):
                for _ in range(20):
                    sink.feed(ms_of(OLD, 10))
                    await asyncio.sleep(0)
                sink.flush()
                sink.feed(ms_of(OLD, 10))
            report = await sink.close()
            assert report.played.nbytes + report.dropped.nbytes == report.fed.nbytes

    async def test_stops_from_two_threads_drop_each_byte_once(self) -> None:
        """Stop during stop: two stop requests drop the queue exactly once."""
        loop = asyncio.get_running_loop()
        for _ in range(self.ROUNDS):
            sink, _ = await open_sink()
            sink.feed(ms_of(OLD, 200))
            dropped: list[int] = []

            def request_stop(
                sink: PcmStreamSink = sink, out: list[int] = dropped
            ) -> None:
                out.append(sink.flush().dropped.nbytes)

            threads = [
                threading.Thread(target=loop.call_soon_threadsafe, args=(request_stop,))
                for _ in range(2)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            while len(dropped) < 2:
                await asyncio.sleep(0)
            assert sorted(dropped) == [0, len(ms_of(OLD, 200))]
            await sink.close()

    async def test_flush_racing_close_never_raises(self) -> None:
        """Stop vs. close: whichever runs first, the other is harmless."""
        for _ in range(self.ROUNDS):
            sink, _ = await open_sink()
            sink.feed(ms_of(OLD, 200))
            closing = asyncio.create_task(sink.close())
            await asyncio.sleep(0)
            sink.flush()
            report = await closing
            assert report.played.nbytes + report.dropped.nbytes == report.fed.nbytes

    async def test_flush_racing_natural_end_always_releases_wait(self) -> None:
        """Stop vs. turn end: whichever wins, the waiter returns."""
        for _ in range(self.ROUNDS):
            sink, device = await open_sink()
            sink.feed(ms_of(OLD, 20))
            sink.finish()
            thread = threading.Thread(target=device.opened.pull, args=(2,))
            thread.start()
            sink.flush()
            thread.join()
            await asyncio.wait_for(sink.wait(), timeout=1)
            report = await sink.close()
            assert report.played.nbytes + report.dropped.nbytes == report.fed.nbytes


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
