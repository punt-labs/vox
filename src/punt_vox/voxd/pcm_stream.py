"""Streaming PCM output: one continuously-owned output stream per utterance.

File playback (:class:`~punt_vox.voxd.playback.PlaybackQueue.play_audio`)
spawns one player process per file, and killing that process does not stop
sound already handed to the OS audio buffer. A conversational agent must go
silent the instant the user barges in, so its audio is written into an output
stream voxd owns and whose pending audio voxd can discard itself
(``docs/conversation-mode-prd.tex``, "Continuous-stream playback").

Thread discipline: :meth:`PcmStreamSink.feed`, :meth:`~PcmStreamSink.flush`
and :meth:`~PcmStreamSink.finish` run on the event loop; the PortAudio
callback runs on PortAudio's own thread. Both sides touch the pending audio
only through :class:`PcmBuffer`, whose single lock makes each append, block
pull and clear atomic. A flush is therefore exact: no audio fed before it
returns is pulled after it, so at most the one block the device already
holds is still heard.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Buffer
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Self, final

from punt_vox.voxd.pcm_audio import (
    BYTES_PER_FRAME,
    PCM_SAMPLE_RATE_HZ,
    PcmBuffer,
    PcmDuration,
)
from punt_vox.voxd.pcm_output import OutputDeviceLostError, run_device_call

if TYPE_CHECKING:
    from punt_vox.voxd.pcm_output import OutputStream, OutputStreamFactory

logger = logging.getLogger(__name__)

__all__ = [
    "FlushReport",
    "PcmStreamSink",
    "StreamReport",
]

_BLOCK_FRAMES = 320
"""20 ms per callback: the most audio that can still sound after a flush."""


@final
@dataclass(frozen=True, slots=True)
class FlushReport:
    """What one :meth:`PcmStreamSink.flush` discarded and how long it took."""

    dropped: PcmDuration
    elapsed_ms: float


@final
@dataclass(frozen=True, slots=True)
class StreamReport:
    """How one utterance's stream ended: audio fed, heard and discarded.

    ``underflows`` counts blocks the device asked for while the response was
    still arriving and that came up short or empty after audio had started.
    ``device_flags`` counts callbacks PortAudio flagged (under/overflow,
    priming), and ``device_lost`` is set when the device stopped on its own.
    """

    fed: PcmDuration
    played: PcmDuration
    dropped: PcmDuration
    interrupted: bool
    underflows: int
    device_flags: int
    device_lost: bool

    @property
    def degraded(self) -> bool:
        """Return whether the device starved, flagged, or stopped on its own."""
        return bool(self.underflows or self.device_flags or self.device_lost)


type SinkPhase = Literal["streaming", "finished", "interrupted", "closed"]
"""Where an utterance is: arriving, fully fed, flushed, or released.

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
        "_device_flags",
        "_device_lost",
        "_done",
        "_drained",
        "_dropped",
        "_fed",
        "_loop",
        "_opened_at",
        "_phase",
        "_played",
        "_stream",
        "_underflows",
    )

    _buffer: PcmBuffer
    _device_flags: int
    _device_lost: bool
    _done: asyncio.Event
    _dropped: int
    _drained: bool
    _fed: int
    _loop: asyncio.AbstractEventLoop
    _opened_at: float
    _phase: SinkPhase
    _played: int
    _stream: OutputStream | None
    _underflows: int

    def __new__(cls, loop: asyncio.AbstractEventLoop) -> Self:
        """Create a sink that signals completion on *loop*."""
        self = super().__new__(cls)
        self._buffer = PcmBuffer()
        self._device_flags = 0
        self._device_lost = False
        self._done = asyncio.Event()
        self._dropped = 0
        self._drained = False
        self._fed = 0
        self._loop = loop
        self._opened_at = time.monotonic()
        self._phase = "streaming"
        self._played = 0
        self._stream = None
        self._underflows = 0
        return self

    @property
    def phase(self) -> SinkPhase:
        """Return where the utterance is in its lifecycle."""
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
        starting, cancelled = await run_device_call(self._start_device, factory)
        if cancelled:
            await self._release_cancelled_open(starting)
            raise asyncio.CancelledError
        self._stream = starting.result()
        logger.info("PCM stream open in %.1f ms", self._since_open_ms())

    def feed(self, pcm: bytes) -> None:
        """Queue one chunk of agent audio.

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
        if self._phase in {"interrupted", "closed"} or (
            self._phase == "finished" and self._drained
        ):
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

    def finish(self) -> None:
        """Mark the response complete: no more :meth:`feed` calls follow.

        A no-op once flushed. A trailing half sample, which a chunk boundary
        can leave, is dropped so the stream can still play out cleanly.
        """
        if self._phase == "interrupted":
            return
        if self._phase != "streaming":
            msg = f"cannot finish a PCM sink that is {self._phase}"
            raise ValueError(msg)
        self._dropped += self._buffer.trim_partial_frame()
        self._phase = "finished"
        if not self._buffer:
            self._drained = True
            self._done.set()

    async def wait(self) -> None:
        """Return once the audio played out, or the sink was flushed or closed.

        Raises :class:`OutputDeviceLostError` if the device stopped first.
        """
        await self._done.wait()
        if self._device_lost:
            msg = "output device stopped before the PCM stream ended"
            raise OutputDeviceLostError(msg)

    async def close(self) -> StreamReport:
        """Release the device off the loop and report how the utterance ended.

        A stream that played out is stopped, so its last block is heard;
        anything else (flushed, or closed early) is aborted at once. The
        device is closed even when stopping it fails or the caller is
        cancelled, and waiters are released.
        """
        if self._phase == "closed":
            msg = "PCM sink is already closed"
            raise ValueError(msg)
        ending, self._phase = self._phase, "closed"
        pending = self._buffer.clear()
        self._dropped += pending
        self._done.set()
        stream, self._stream = self._stream, None
        try:
            if stream is not None:
                drain = ending == "finished" and not pending
                releasing, cancelled = await run_device_call(
                    self._release_device, stream, drain=drain
                )
                error = releasing.exception()
                if cancelled:
                    if error is not None:
                        logger.error(
                            "releasing the PCM device during a cancelled close",
                            exc_info=error,
                        )
                    raise asyncio.CancelledError
                if error is not None:
                    raise error
        finally:
            report = self._report(interrupted=ending == "interrupted")
        return report

    def _report(self, *, interrupted: bool) -> StreamReport:
        """Build and log the closing report, at WARNING if playback degraded."""
        report = StreamReport(
            fed=PcmDuration(self._fed),
            played=PcmDuration(self._played),
            dropped=PcmDuration(self._dropped),
            interrupted=interrupted,
            underflows=self._underflows,
            device_flags=self._device_flags,
            device_lost=self._device_lost,
        )
        logger.log(
            logging.WARNING if report.degraded else logging.INFO,
            "PCM stream closed at %.0f ms: fed %.0f ms, played %.0f ms, "
            "dropped %.0f ms, interrupted=%s, underflows=%d, device_flags=%d, "
            "device_lost=%s",
            self._since_open_ms(),
            report.fed.ms,
            report.played.ms,
            report.dropped.ms,
            report.interrupted,
            report.underflows,
            report.device_flags,
            report.device_lost,
        )
        return report

    @classmethod
    async def _release_cancelled_open(
        cls, starting: asyncio.Future[OutputStream]
    ) -> None:
        """Close the device a cancelled :meth:`open` was still starting."""
        error = starting.exception()
        if error is None:
            releasing, _ = await run_device_call(
                cls._release_device, starting.result(), drain=False
            )
            error = releasing.exception()
        if error is not None:
            logger.error(
                "releasing the PCM device after a cancelled open", exc_info=error
            )

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

    @staticmethod
    def _release_device(stream: OutputStream, *, drain: bool) -> None:
        """Worker thread: stop or abort the stream, then always close it."""
        try:
            if drain:
                stream.stop()
            else:
                stream.abort()
        finally:
            stream.close()

    def _since_open_ms(self) -> float:
        return (time.monotonic() - self._opened_at) * 1000

    def _device_stopped(self) -> None:
        """Device thread: the stream went inactive; flag it unless it was expected.

        A stream voxd closed, or one whose audio already played out, ended
        normally. At daemon shutdown the loop may already be closed.
        """
        if self._phase == "closed" or self._drained:
            return
        self._device_lost = True
        with contextlib.suppress(RuntimeError):
            self._loop.call_soon_threadsafe(self._done.set)

    def _callback(
        self, outdata: Buffer, frames: int, _time: object, status: object
    ) -> None:
        """PortAudio thread: fill one block, logging any failure before PortAudio."""
        try:
            self._fill(outdata, frames, status)
        except BaseException:
            logger.exception("PCM output callback failed; the device will stop")
            raise

    def _fill(self, outdata: Buffer, frames: int, status: object) -> None:
        """Fill one block, padding with silence, and track the utterance's end."""
        out = memoryview(outdata).cast("B")
        wanted = frames * BYTES_PER_FRAME
        block = self._buffer.pull(wanted)
        out[: len(block)] = block
        out[len(block) : wanted] = bytes(wanted - len(block))
        if status:
            self._device_flags += 1
        started = self._played > 0 or bool(block)
        self._played += len(block)
        if len(block) == wanted or self._drained:
            return
        phase = self._phase
        if phase == "streaming":
            # Starved mid-response: the network fell behind real time.
            if started:
                self._underflows += 1
            return
        if phase == "finished":
            self._drained = True
            self._loop.call_soon_threadsafe(self._done.set)
