"""One open microphone stream, delivering fixed-duration PCM chunks."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Buffer
from typing import TYPE_CHECKING, Self, final

from punt_vox.voxd.conversation_mode.audio_chunk import AudioChunk

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    # Type-only: the name is declared in ``typings/sounddevice/__init__.pyi``
    # for the type checkers; the stub-free runtime module has no such attribute.
    from sounddevice import CallbackFlags

    from punt_vox.voxd.conversation_mode.mic_audio_source import (
        CaptureBackend,
        InputStreamHandle,
    )

__all__ = ["MicCapture"]

logger = logging.getLogger(__name__)


@final
class MicCapture:
    """A started capture: the device is held from :meth:`open` to :meth:`close`.

    The backend's callback runs on its own audio thread, never the event
    loop's; it is bridged with ``loop.call_soon_threadsafe`` onto an
    :class:`asyncio.Queue` the consuming coroutine reads.
    """

    __slots__ = ("_chunk_s", "_queue", "_stream")
    _stream: InputStreamHandle
    _queue: asyncio.Queue[bytes]
    _chunk_s: float

    def __new__(
        cls, stream: InputStreamHandle, queue: asyncio.Queue[bytes], chunk_s: float
    ) -> Self:
        """Hold an entered ``stream`` whose callback feeds ``queue``."""
        self = super().__new__(cls)
        self._stream, self._queue, self._chunk_s = stream, queue, chunk_s
        return self

    @classmethod
    def open(
        cls, backend: CaptureBackend, *, sample_rate_hz: int, chunk_s: float
    ) -> Self:
        """Query the default device and start a stream on it, or raise.

        Raises:
            OSError: When the backend has no default input device, or the
                device refuses the stream.
        """
        block_frames = max(1, round(chunk_s * sample_rate_hz))
        device = backend.device()
        logger.info(
            "Call input device: %s (default %.0f Hz), capturing at %d Hz",
            device.name,
            device.default_samplerate_hz,
            sample_rate_hz,
        )
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[bytes] = asyncio.Queue()

        def _callback(
            indata: Buffer, _frames: int, _time: object, status: CallbackFlags
        ) -> None:
            if status.input_overflow:
                logger.warning("mic capture: input overflow (callback ran late)")
            loop.call_soon_threadsafe(queue.put_nowait, bytes(indata))

        stream = backend.open(
            samplerate=float(sample_rate_hz), blocksize=block_frames, callback=_callback
        )
        return cls(stream.__enter__(), queue, chunk_s)

    async def chunks(self) -> AsyncGenerator[AudioChunk]:
        """Yield one :class:`AudioChunk` per delivered block, until closed.

        Every chunk carries ``duration_s=chunk_s`` regardless of the block's
        wall-clock arrival: the block size is fixed and PortAudio delivers it
        at a steady cadence. A late callback (``status.input_overflow``) is
        logged, not measured into a corrected duration.
        """
        while True:
            pcm = await self._queue.get()
            yield AudioChunk(pcm=pcm, duration_s=self._chunk_s)

    def close(self) -> None:
        """Release the device; idempotent for a stream already closed."""
        self._stream.__exit__(None, None, None)
