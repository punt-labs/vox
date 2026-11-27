"""Streaming PCM output: one continuously-owned output stream per sink.

File playback (:class:`~punt_vox.voxd.playback.PlaybackQueue.play_audio`)
spawns one player process per file, and killing that process does not stop
sound already handed to the OS audio buffer. A conversational agent must go
silent the instant the user barges in, so its audio is written into an output
stream voxd owns and whose pending audio voxd can discard itself
(``docs/conversation-mode-prd.tex``, "Continuous-stream playback").

A sink owns one output stream from its first reply to a barge-in (or the
call's hang-up), so it spans every agent response in between, silences
included; its :class:`~punt_vox.voxd.pcm_report.StreamReport` covers that
whole stretch.

Thread discipline: :meth:`PcmStreamSink.feed`, :meth:`~PcmStreamSink.flush`
and :meth:`~PcmStreamSink.finish` run on the event loop; the PortAudio
callback runs on PortAudio's own thread. Both sides touch the pending audio
only through :class:`PcmBuffer`, whose single lock makes each append, block
pull and clear atomic. A flush is therefore exact: no audio fed before it
returns is pulled after it, so what is still heard is the one block the
device already holds and a fade-out tail of at most 3 ms.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Buffer
from typing import TYPE_CHECKING, Literal, Self, final

from punt_vox.voxd.pcm_audio import (
    PCM_SAMPLE_RATE_HZ,
    PcmBuffer,
    PcmDuration,
)
from punt_vox.voxd.pcm_gap import GapSmoother
from punt_vox.voxd.pcm_output import DeviceCall, OutputDeviceLostError
from punt_vox.voxd.pcm_release import StreamRelease
from punt_vox.voxd.pcm_report import FlushReport, StreamReport

if TYPE_CHECKING:
    from punt_vox.voxd.pcm_output import OutputStream, OutputStreamFactory

logger = logging.getLogger(__name__)

__all__ = ["PcmStreamSink"]

_BLOCK_FRAMES = 320
"""20 ms per callback: the most audio the device holds when a flush lands.

After a flush only that block and a fade-out tail of at most 3 ms (one more
block at the very end of it) can still sound."""


_TAIL_WAIT_S = 2 * _BLOCK_FRAMES / PCM_SAMPLE_RATE_HZ
"""40 ms: two callbacks, enough for the one after a flush to write its tail."""

_STALL_WAIT_S = 1.0
"""A device that has made no callback for this long is stalled, not slow."""

type SinkPhase = Literal["streaming", "finished", "interrupted", "closed"]
"""Where a sink is: arriving, fully fed, flushed, or released.

``streaming -> finished`` (:meth:`~PcmStreamSink.finish`),
``streaming | finished -> interrupted`` (:meth:`~PcmStreamSink.flush`), and
any phase ``-> closed`` (:meth:`~PcmStreamSink.close`). Only the event loop
writes the phase; the device thread reads it. ``docs/pcm-stream.tex`` models
the transitions and the invariants the tests name.
"""


@final
class PcmStreamSink:
    """Play streamed ``pcm_16000`` audio, with an immediate :meth:`flush`.

    Built and held by :meth:`PlaybackQueue.stream_pcm
    <punt_vox.voxd.playback.PlaybackQueue.stream_pcm>`, which owns the
    playback mutex for the sink's lifetime.
    """

    __slots__ = (
        "_buffer",
        "_callbacks",
        "_device_flags",
        "_device_lost",
        "_done",
        "_drained",
        "_dropped",
        "_fed",
        "_gap",
        "_loop",
        "_opened_at",
        "_phase",
        "_played",
        "_stream",
        "_tail_played",
    )

    _buffer: PcmBuffer
    _callbacks: int
    _device_flags: int
    _device_lost: bool
    _done: asyncio.Event
    _dropped: int
    _drained: bool
    _fed: int
    _gap: GapSmoother
    _loop: asyncio.AbstractEventLoop
    _opened_at: float
    _phase: SinkPhase
    _played: int
    _stream: OutputStream | None
    _tail_played: asyncio.Event

    def __new__(cls, loop: asyncio.AbstractEventLoop) -> Self:
        """Create a sink that signals completion on *loop*."""
        self = super().__new__(cls)
        self._buffer = PcmBuffer()
        self._callbacks = 0
        self._device_flags = 0
        self._device_lost = False
        self._done = asyncio.Event()
        self._dropped = 0
        self._drained = False
        self._fed = 0
        self._gap = GapSmoother()
        self._loop = loop
        self._opened_at = time.monotonic()
        self._phase = "streaming"
        self._played = 0
        self._stream = None
        self._tail_played = asyncio.Event()
        return self

    @property
    def phase(self) -> SinkPhase:
        """Return where the sink is in its lifecycle."""
        return self._phase

    @property
    def played(self) -> PcmDuration:
        """Return how much audio the device has pulled so far."""
        return PcmDuration(self._played)

    async def open(self, factory: OutputStreamFactory) -> None:
        """Open and start the device stream off the loop; it plays silence."""
        if self._phase != "streaming" or self._stream is not None:
            msg = f"cannot open a PCM sink that is {self._phase}"
            raise ValueError(msg)
        self._opened_at = time.monotonic()
        starting = await DeviceCall.run(self._start_device, factory)
        if starting.cancelled:
            await StreamRelease.after_cancelled_open(starting)
            raise asyncio.CancelledError
        self._stream = starting.result()
        logger.info("PCM stream open in %.1f ms", self._since_open_ms())

    def feed(self, pcm: bytes, *, starts_utterance: bool = False) -> None:
        """Queue one chunk of agent audio.

        The stream stays open between the agent's turns and across its tool
        calls, so only the caller knows where an utterance begins. Set
        *starts_utterance* on the chunk that begins one: a silence that ends
        there is a pause, not a stall.

        After a flush, audio still in flight from the interrupted response is
        counted as dropped and never queued. Feeding a finished or closed
        sink is a caller bug and raises :class:`ValueError`; feeding after
        the device stopped on its own raises :class:`OutputDeviceLostError`.
        """
        if self._device_lost:
            msg = "output device stopped; the PCM stream cannot play more audio"
            raise OutputDeviceLostError(msg)
        if self._phase not in {"streaming", "interrupted"}:
            msg = f"cannot feed a PCM sink that is {self._phase}"
            raise ValueError(msg)
        if starts_utterance:
            # Before the audio is queued: a block that pulls it must find the mark.
            self._gap.note_boundary(self._fed)
        self._fed += len(pcm)
        if self._phase == "interrupted":
            self._dropped += len(pcm)
            return
        self._buffer.append(pcm)

    def flush(self) -> FlushReport:
        """Discard all pending audio now (an ElevenLabs ``interruption``).

        Idempotent: flushing a sink that is already interrupted, closed, or
        played out drops nothing and reports zero, so a stop that races
        another stop or the natural end of the turn is harmless.
        """
        started = time.monotonic()
        if self._is_settled:
            return FlushReport(PcmDuration(0), 0.0)
        dropped = PcmDuration(self._buffer.clear())
        self._dropped += dropped.nbytes
        self._phase = "interrupted"
        self._done.set()
        report = FlushReport(dropped, (time.monotonic() - started) * 1000)
        logger.info(
            "PCM flush at %.0f ms: dropped %.0f ms of queued audio in %.3f ms",
            self._since_open_ms(),
            report.dropped.ms,
            report.elapsed_ms,
        )
        return report

    @property
    def _is_settled(self) -> bool:
        """Return whether nothing is left for a flush to discard."""
        return self._phase in {"interrupted", "closed"} or (
            self._phase == "finished" and self._drained
        )

    def finish(self) -> None:
        """Mark the response complete: no more :meth:`feed` calls follow.

        A no-op once flushed. A trailing half sample, which a chunk boundary
        can leave, is dropped so the stream can still play out cleanly. The
        stream counts as played out when the next callback has shaped the last
        block and its fade-out: an empty buffer here does not say so, since a
        callback may hold the final block unshaped.
        """
        if self._phase == "interrupted":
            return
        if self._phase != "streaming":
            msg = f"cannot finish a PCM sink that is {self._phase}"
            raise ValueError(msg)
        self._dropped += self._buffer.trim_partial_frame()
        self._phase = "finished"
        if self._stream is None:
            # No callback will ever pull: with a device, the callback that
            # shaped the last block is the one to say the audio is played out.
            self._drained = True
            self._done.set()

    async def wait(self) -> None:
        """Return once the audio played out, or the sink was flushed or closed.

        Raises :class:`OutputDeviceLostError` if the device stopped first, or
        was silent for at least one stall period (the check runs once per
        period, so it fires after one to two): a hang-up must not wait on a
        device that has gone away without saying so.
        """
        calls = -1
        while not self._done.is_set():
            calls = await self._wait_or_stall(calls)
        if self._device_lost:
            msg = "output device stopped before the PCM stream ended"
            raise OutputDeviceLostError(msg)

    async def _wait_or_stall(self, calls: int) -> int:
        """Wait one stall period; return the callback count, or raise if frozen."""
        try:
            async with asyncio.timeout(_STALL_WAIT_S):
                await self._done.wait()
        except TimeoutError:
            if self._callbacks == calls:
                logger.warning(
                    "PCM output made no callback for at least %.3g s; "
                    "giving up the wait",
                    _STALL_WAIT_S,
                )
                # Release with abort(): stop() waits on the device to drain.
                self._device_lost = True
                msg = "output device made no progress; the PCM stream stalled"
                raise OutputDeviceLostError(msg) from None
        return self._callbacks

    async def close(self) -> StreamReport:
        """Release the device off the loop and report how the stream ended.

        A stream that played out is stopped, so its last block is heard;
        anything else (flushed, or closed early) is aborted at once. The
        device is closed even when stopping it fails or the caller is
        cancelled, and waiters are released.
        """
        if self._phase == "closed":
            msg = "PCM sink is already closed"
            raise ValueError(msg)
        try:
            is_tail_played = await self._play_flush_tail()
        except asyncio.CancelledError:
            # The device must still be released: nobody calls close() again.
            # A failed release is logged; it must not replace the cancellation.
            try:
                await self._release(is_tail_played=False)
            except Exception:
                logger.exception("releasing the PCM device during a cancelled close")
            raise
        return await self._release(is_tail_played=is_tail_played)

    async def _release(self, *, is_tail_played: bool) -> StreamReport:
        """Close the sink, release its device, and report how the stream ended."""
        ending, self._phase = self._phase, "closed"
        pending = self._buffer.clear()
        self._dropped += pending
        self._done.set()
        stream, self._stream = self._stream, None
        try:
            if stream is not None:
                is_drained = ending == "finished" and not pending
                drain = (is_tail_played or is_drained) and not self._device_lost
                await StreamRelease.run(stream, drain=drain)
        finally:
            report = self._report(interrupted=ending == "interrupted")
        return report

    async def _play_flush_tail(self) -> bool:
        """Wait, for at most two blocks, for a flushed stream's fade-out to play.

        ``abort`` would discard the tail the device thread writes after a
        flush, so a stream whose tail played is stopped instead, which plays
        what the device holds: at most the one block it already had, then the
        tail. A device that never calls back is aborted after the wait.
        """
        if self._phase != "interrupted" or self._stream is None:
            return False
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(_TAIL_WAIT_S):
                await self._tail_played.wait()
            return True
        return False

    def _report(self, *, interrupted: bool) -> StreamReport:
        """Build and log the closing report, at WARNING if playback degraded."""
        self._gap.settle()
        report = StreamReport(
            fed=PcmDuration(self._fed),
            played=PcmDuration(self._played),
            dropped=PcmDuration(self._dropped),
            interrupted=interrupted,
            underflows=self._gap.starved,
            gap_total=self._gap.total,
            gap_longest=self._gap.longest,
            idle_gaps=self._gap.idle_gaps,
            trailing_gap=self._gap.trailing,
            device_flags=self._device_flags,
            device_lost=self._device_lost,
        )
        logger.log(
            logging.WARNING if report.degraded else logging.INFO,
            "%s",
            report.describe(self._since_open_ms()),
        )
        return report

    def _start_device(self, factory: OutputStreamFactory) -> OutputStream:
        """Worker thread: create and start the stream, closing it on failure."""
        stream = factory(
            samplerate=PCM_SAMPLE_RATE_HZ,
            blocksize=_BLOCK_FRAMES,
            callback=self._callback,
            finished_callback=self._device_stopped,
        )
        try:
            stream.start()
        except BaseException:
            try:
                stream.close()
            except Exception:
                logger.exception("closing the PCM device after a failed start")
            raise
        return stream

    def _since_open_ms(self) -> float:
        return (time.monotonic() - self._opened_at) * 1000

    def _device_stopped(self) -> None:
        """Device thread: the stream went inactive; flag it unless it was expected.

        A stream voxd closed, or one whose audio already played out, ended
        normally. At daemon shutdown the loop may already be closed while
        the device still runs; that is the normal end, not a fault.
        """
        if self._phase == "closed" or self._drained:
            return
        self._device_lost = True
        with contextlib.suppress(RuntimeError):
            self._loop.call_soon_threadsafe(self._done.set)

    def _callback(
        self, outdata: Buffer, _frames: int, _time: object, status: object
    ) -> None:
        """PortAudio thread: fill one block, logging any failure before PortAudio."""
        try:
            if status:
                self._device_flags += 1
            self._fill(outdata)
        except BaseException:
            logger.exception("PCM output callback failed; the device will stop")
            raise

    def _count_block(self, nbytes: int) -> None:
        """Device thread: add one pulled block to the stream's totals.

        The first block with audio in it makes playback audible, so a barge-in
        from here on cuts a reply the person has heard.
        """
        if nbytes and not self._played:
            with contextlib.suppress(RuntimeError):  # a closed loop at shutdown
                self._loop.call_soon_threadsafe(
                    logger.info,
                    "PCM playback started at %.0f ms",
                    self._since_open_ms(),
                )
        self._played += nbytes

    def _fill(self, outdata: Buffer) -> None:
        """Fill one block, padding with silence, and track the end of the stream."""
        out = memoryview(outdata).cast("B")  # PortAudio hands over one block exactly
        filled = self._buffer.pull_into(out)
        self._callbacks += 1
        offset = self._played
        self._count_block(filled)
        is_short = filled < len(out) and not self._drained
        phase = self._phase
        # Starved mid-response: the network fell behind real time. A short
        # block before any audio played is the response arriving.
        starving = is_short and phase == "streaming" and bool(self._played)
        self._gap.shape(out, filled, starving=starving, played=offset)
        if not self._gap.needs_fade_out:
            self._settle_block(phase, is_short=is_short)

    def _settle_block(self, phase: SinkPhase, *, is_short: bool) -> None:
        """Device thread: wake whoever waits on the end of a block with no tail left."""
        if is_short and phase == "finished":
            self._drained = True
            self._signal(self._loop, self._done)
        elif phase == "interrupted":
            self._signal(self._loop, self._tail_played)

    @staticmethod
    def _signal(loop: asyncio.AbstractEventLoop, event: asyncio.Event) -> None:
        """Device thread: set *event* on *loop*; a closed loop at shutdown is fine."""
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(event.set)
