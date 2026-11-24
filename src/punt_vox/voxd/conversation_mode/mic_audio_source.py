"""Live microphone capture, yielding :class:`AudioChunk` at a fixed cadence.

The backend that names the device and opens the stream is injected; the
production one is :class:`~.input_device.PortAudio`. The callback the backend
invokes runs on its own audio thread, never the event loop's thread --
:meth:`MicAudioSource.chunks` bridges that with ``loop.call_soon_threadsafe``
onto an :class:`asyncio.Queue`, the same pattern a WebSocket server uses to
accept work from a non-asyncio thread.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Buffer
from typing import TYPE_CHECKING, Protocol, Self, final, runtime_checkable

from punt_vox.voxd.conversation_mode.audio_chunk import SAMPLE_RATE_HZ, AudioChunk

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    # Type-only: neither name exists on the real, stub-free ``sounddevice``
    # runtime module -- both are declared in ``typings/sounddevice/__init__.pyi``
    # for the type checkers only. Importing either unconditionally would raise
    # ``ImportError`` the moment this module is loaded.
    from sounddevice import CallbackFlags, RawInputCallback

    from punt_vox.voxd.conversation_mode.input_device import InputDevice

logger = logging.getLogger(__name__)

__all__ = ["MicAudioSource"]

# Match the voice call transport's 20 ms audio frames.
_CHUNK_S = 0.02


@runtime_checkable
class InputStreamHandle(Protocol):
    """The context-manager surface :class:`MicAudioSource` needs from a stream."""

    def __enter__(self) -> Self: ...

    def __exit__(self, *exc_info: object) -> None: ...


class CaptureBackend(Protocol):
    """Names the default input device and opens a raw stream on it."""

    def device(self) -> InputDevice: ...

    def open(
        self, *, samplerate: float, blocksize: int, callback: RawInputCallback
    ) -> InputStreamHandle: ...


@final
class MicAudioSource:
    """Yield fixed-duration PCM chunks from the system microphone.

    The consumer closes the async generator when the call ends; exiting the
    stream context releases the audio device.
    """

    __slots__ = ("_backend", "_chunk_s", "_sample_rate_hz")
    _backend: CaptureBackend
    _sample_rate_hz: int
    _chunk_s: float

    def __new__(
        cls,
        backend: CaptureBackend,
        *,
        sample_rate_hz: int = SAMPLE_RATE_HZ,
        chunk_s: float = _CHUNK_S,
    ) -> Self:
        """Create a mic audio source over ``backend``."""
        self = super().__new__(cls)
        self._backend = backend
        self._sample_rate_hz = sample_rate_hz
        self._chunk_s = chunk_s
        return self

    async def chunks(self) -> AsyncGenerator[AudioChunk]:
        """Capture indefinitely, yielding one :class:`AudioChunk` per block.

        Every chunk carries ``duration_s=self._chunk_s`` regardless of the
        block's actual wall-clock arrival time -- the same caller-supplied-
        timing contract :class:`AudioChunk` documents, satisfied here
        because the block size is fixed and PortAudio delivers it at a
        steady cadence; a genuinely late callback (an overrun PortAudio
        reports via ``status.input_overflow``) is logged, not measured into
        a corrected duration, since a live call has no test harness to
        replay a corrected value against.

        Raises:
            OSError: When the backend has no default input device.
        """
        block_frames = max(1, round(self._chunk_s * self._sample_rate_hz))
        device = self._backend.device()
        logger.info(
            "Call input device: %s (default %.0f Hz), capturing at %d Hz",
            device.name,
            device.default_samplerate_hz,
            self._sample_rate_hz,
        )
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[bytes] = asyncio.Queue()

        def _callback(
            indata: Buffer, _frames: int, _time: object, status: CallbackFlags
        ) -> None:
            if status.input_overflow:
                logger.warning("mic capture: input overflow (callback ran late)")
            loop.call_soon_threadsafe(queue.put_nowait, bytes(indata))

        with self._backend.open(
            samplerate=float(self._sample_rate_hz),
            blocksize=block_frames,
            callback=_callback,
        ):
            while True:
                pcm = await queue.get()
                yield AudioChunk(pcm=pcm, duration_s=self._chunk_s)
