"""Live microphone capture, yielding :class:`AudioChunk` at a fixed cadence.

``sounddevice`` (PortAudio bindings) is the capture backend -- cross-platform
by construction, so there is no macOS/Linux dispatch here the way
``providers/say.py`` vs. ``providers/espeak.py`` need one for playback: both
platforms reach the same :class:`~sounddevice.RawInputStream` class, and
PortAudio itself resolves to CoreAudio or ALSA underneath. PortAudio's C
library is a separate runtime dependency from the Python package (Homebrew's
``portaudio`` on macOS, ``libportaudio2`` on most Linux distributions) --
the same shape as ``pydub``'s ffmpeg dependency, which this project already
accepts.

The callback PortAudio invokes runs on its own audio thread, never the event
loop's thread -- :meth:`MicAudioSource.chunks` bridges that with
``loop.call_soon_threadsafe`` onto an :class:`asyncio.Queue`, the same
pattern a WebSocket server uses to accept work from a non-asyncio thread.
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

logger = logging.getLogger(__name__)

__all__ = ["MicAudioSource"]

# Match the voice call transport's 20 ms audio frames.
_CHUNK_S = 0.02


@runtime_checkable
class InputStreamFactory(Protocol):
    """Constructs a :class:`~sounddevice.RawInputStream`-shaped context manager.

    The production implementation opens the default system microphone.
    """

    def __call__(
        self,
        *,
        samplerate: float,
        blocksize: int,
        callback: RawInputCallback,
    ) -> InputStreamHandle: ...


@runtime_checkable
class InputStreamHandle(Protocol):
    """The context-manager surface :class:`MicAudioSource` needs from a stream."""

    def __enter__(self) -> Self: ...

    def __exit__(self, *exc_info: object) -> None: ...


@final
class MicAudioSource:
    """Yield fixed-duration PCM chunks from the system microphone.

    The consumer closes the async generator when the call ends; exiting the
    stream context releases the audio device.
    """

    __slots__ = ("_chunk_s", "_input_stream_factory", "_sample_rate_hz")
    _sample_rate_hz: int
    _chunk_s: float
    _input_stream_factory: InputStreamFactory

    def __new__(
        cls,
        *,
        sample_rate_hz: int = SAMPLE_RATE_HZ,
        chunk_s: float = _CHUNK_S,
        input_stream_factory: InputStreamFactory | None = None,
    ) -> Self:
        """Create a mic audio source."""
        self = super().__new__(cls)
        self._sample_rate_hz = sample_rate_hz
        self._chunk_s = chunk_s
        self._input_stream_factory = input_stream_factory or cls._open_stream
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
        """
        block_frames = max(1, round(self._chunk_s * self._sample_rate_hz))
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[bytes] = asyncio.Queue()

        def _callback(
            indata: Buffer, _frames: int, _time: object, status: CallbackFlags
        ) -> None:
            if status.input_overflow:
                logger.warning("mic capture: input overflow (callback ran late)")
            loop.call_soon_threadsafe(queue.put_nowait, bytes(indata))

        with self._input_stream_factory(
            samplerate=float(self._sample_rate_hz),
            blocksize=block_frames,
            callback=_callback,
        ):
            while True:
                pcm = await queue.get()
                yield AudioChunk(pcm=pcm, duration_s=self._chunk_s)

    @staticmethod
    def _open_stream(
        *, samplerate: float, blocksize: int, callback: RawInputCallback
    ) -> InputStreamHandle:
        """Open a raw mono PCM stream, loading optional PortAudio on demand."""
        from sounddevice import RawInputStream

        return RawInputStream(
            samplerate=samplerate,
            blocksize=blocksize,
            channels=1,
            dtype="int16",
            callback=callback,
        )
