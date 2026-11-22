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
import logging
import threading
import time
from collections.abc import Buffer, Callable
from dataclasses import dataclass
from typing import Protocol, Self, final

logger = logging.getLogger(__name__)

__all__ = [
    "PCM_SAMPLE_RATE_HZ",
    "FlushReport",
    "OutputStream",
    "OutputStreamFactory",
    "PcmBuffer",
    "PcmDuration",
    "PcmStreamSink",
    "StreamReport",
    "sounddevice_output_stream",
]

PCM_SAMPLE_RATE_HZ = 16_000
"""ElevenLabs ``pcm_16000``: 16-bit signed little-endian mono at 16 kHz."""

_BYTES_PER_FRAME = 2
_BLOCK_FRAMES = 320
"""20 ms per callback: the most audio that can still sound after a flush."""


@final
@dataclass(frozen=True, slots=True)
class PcmDuration:
    """A byte count of ``pcm_16000`` audio and the playing time it spans."""

    nbytes: int

    @property
    def ms(self) -> float:
        """Return how long this many bytes play for, in milliseconds."""
        return self.nbytes / _BYTES_PER_FRAME / PCM_SAMPLE_RATE_HZ * 1000


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
    still arriving but only part of the block had come in.
    """

    fed: PcmDuration
    played: PcmDuration
    dropped: PcmDuration
    interrupted: bool
    underflows: int


class OutputStream(Protocol):
    """The slice of a PortAudio raw output stream the sink drives."""

    def start(self) -> None:
        """Begin invoking the callback."""
        ...

    def stop(self) -> None:
        """Stop after the device has played every block already pulled."""
        ...

    def abort(self) -> None:
        """Stop at once, discarding what the device has not yet played."""
        ...

    def close(self) -> None:
        """Release the device."""
        ...


type OutputCallback = Callable[[Buffer, int, object, object], None]


class OutputStreamFactory(Protocol):
    """Open a mono int16 output stream that pulls audio through *callback*."""

    def __call__(
        self, *, samplerate: int, blocksize: int, callback: OutputCallback
    ) -> OutputStream:
        """Return an unstarted stream."""
        ...


def sounddevice_output_stream(
    *, samplerate: int, blocksize: int, callback: OutputCallback
) -> OutputStream:
    """Open the default PortAudio output device.

    Imports ``sounddevice`` here, not at module scope: it is the optional
    ``call`` extra, and every ``voxd`` start imports this module.
    """
    from sounddevice import RawOutputStream

    return RawOutputStream(
        samplerate=samplerate,
        blocksize=blocksize,
        channels=1,
        dtype="int16",
        latency="low",
        callback=callback,
    )


@final
class PcmBuffer:
    """Thread-safe FIFO of PCM bytes shared by the loop and the device thread."""

    __slots__ = ("_lock", "_pending")

    _lock: threading.Lock
    _pending: bytearray

    def __new__(cls) -> Self:
        """Create an empty buffer."""
        self = super().__new__(cls)
        self._lock = threading.Lock()
        self._pending = bytearray()
        return self

    def append(self, pcm: bytes) -> None:
        """Queue *pcm* behind everything already pending."""
        with self._lock:
            self._pending += pcm

    def pull(self, nbytes: int) -> bytes:
        """Remove and return up to *nbytes* whole frames from the front."""
        with self._lock:
            take = min(nbytes, len(self._pending))
            take -= take % _BYTES_PER_FRAME
            block = bytes(self._pending[:take])
            del self._pending[:take]
            return block

    def clear(self) -> int:
        """Discard everything pending and return how many bytes were dropped."""
        with self._lock:
            dropped = len(self._pending)
            self._pending.clear()
            return dropped

    def __len__(self) -> int:
        """Return the number of bytes pending."""
        with self._lock:
            return len(self._pending)


@final
class PcmStreamSink:
    """Play streamed ``pcm_16000`` audio, with an immediate :meth:`flush`.

    Built and held by :meth:`PlaybackQueue.stream_pcm
    <punt_vox.voxd.playback.PlaybackQueue.stream_pcm>`, which owns the
    playback mutex for the sink's lifetime.
    """

    __slots__ = (
        "_buffer",
        "_done",
        "_drained",
        "_dropped",
        "_fed",
        "_finished",
        "_interrupted",
        "_loop",
        "_opened_at",
        "_played",
        "_stream",
        "_underflows",
    )

    _buffer: PcmBuffer
    _done: asyncio.Event
    _dropped: int
    _drained: bool
    _fed: int
    _finished: bool
    _interrupted: bool
    _loop: asyncio.AbstractEventLoop
    _opened_at: float
    _played: int
    _stream: OutputStream | None
    _underflows: int

    def __new__(cls, loop: asyncio.AbstractEventLoop) -> Self:
        """Create a sink that signals completion on *loop*."""
        self = super().__new__(cls)
        self._buffer = PcmBuffer()
        self._done = asyncio.Event()
        self._dropped = 0
        self._drained = False
        self._fed = 0
        self._finished = False
        self._interrupted = False
        self._loop = loop
        self._opened_at = 0.0
        self._played = 0
        self._stream = None
        self._underflows = 0
        return self

    @property
    def played(self) -> PcmDuration:
        """Return how much audio the device has pulled so far."""
        return PcmDuration(self._played)

    def open(self, factory: OutputStreamFactory) -> None:
        """Open and start the device stream; it plays silence until fed."""
        started = time.monotonic()
        stream = factory(
            samplerate=PCM_SAMPLE_RATE_HZ,
            blocksize=_BLOCK_FRAMES,
            callback=self._callback,
        )
        try:
            stream.start()
        except BaseException:
            stream.close()
            raise
        self._stream = stream
        self._opened_at = time.monotonic()
        logger.info("PCM stream open in %.1f ms", (self._opened_at - started) * 1000)

    def feed(self, pcm: bytes) -> None:
        """Queue one chunk of agent audio; ignored once flushed or finished."""
        if self._interrupted or self._finished:
            return
        self._fed += len(pcm)
        self._buffer.append(pcm)

    def flush(self) -> FlushReport:
        """Discard all pending audio now (an ElevenLabs ``interruption``).

        Idempotent: a second flush drops nothing and reports zero. Later
        :meth:`feed` calls are ignored, so audio still in flight from the
        interrupted response cannot resume playback.
        """
        started = time.monotonic()
        dropped = PcmDuration(self._buffer.clear())
        self._dropped += dropped.nbytes
        first = not self._interrupted
        self._interrupted = True
        self._done.set()
        report = FlushReport(dropped, (time.monotonic() - started) * 1000)
        if first:
            logger.info(
                "PCM flush at %.0f ms: dropped %.0f ms of queued audio in %.3f ms",
                self._since_open_ms(),
                report.dropped.ms,
                report.elapsed_ms,
            )
        return report

    def finish(self) -> None:
        """Mark the response complete: no more :meth:`feed` calls follow."""
        self._finished = True
        if not self._buffer:
            self._done.set()

    async def wait(self) -> None:
        """Return once the audio has played out or the sink was flushed."""
        await self._done.wait()

    def close(self) -> StreamReport:
        """Release the device and report how the utterance ended.

        A stream that played out is stopped, so its last block is heard;
        anything else (flushed, or closed early) is aborted at once.
        """
        stream, self._stream = self._stream, None
        self._dropped += self._buffer.clear()
        if stream is not None:
            if self._drained and not self._interrupted:
                stream.stop()
            else:
                stream.abort()
            stream.close()
        report = StreamReport(
            fed=PcmDuration(self._fed),
            played=PcmDuration(self._played),
            dropped=PcmDuration(self._dropped),
            interrupted=self._interrupted,
            underflows=self._underflows,
        )
        logger.info(
            "PCM stream closed at %.0f ms: fed %.0f ms, played %.0f ms, "
            "dropped %.0f ms, interrupted=%s, underflows=%d",
            self._since_open_ms(),
            report.fed.ms,
            report.played.ms,
            report.dropped.ms,
            report.interrupted,
            report.underflows,
        )
        return report

    def _since_open_ms(self) -> float:
        return (time.monotonic() - self._opened_at) * 1000

    def _callback(
        self, outdata: Buffer, frames: int, _time: object, _status: object
    ) -> None:
        """PortAudio thread: fill one block, padding with silence."""
        out = memoryview(outdata).cast("B")
        wanted = frames * _BYTES_PER_FRAME
        block = self._buffer.pull(wanted)
        out[: len(block)] = block
        out[len(block) : wanted] = bytes(wanted - len(block))
        self._played += len(block)
        if len(block) == wanted or self._drained or self._interrupted:
            return
        if not self._finished:
            # Starved mid-response: the network fell behind real time.
            if block:
                self._underflows += 1
            return
        self._drained = True
        self._loop.call_soon_threadsafe(self._done.set)
