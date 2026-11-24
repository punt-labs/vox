"""One open microphone stream, delivering fixed-duration PCM chunks."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Buffer
from typing import TYPE_CHECKING, ClassVar, Self, final

from punt_vox.voxd.conversation_mode.audio_chunk import AudioChunk
from punt_vox.voxd.conversation_mode.block_queue import BlockQueue
from punt_vox.voxd.log_text import LogText

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
    loop's; it is bridged with ``loop.call_soon_threadsafe`` onto a
    :class:`BlockQueue` the consuming coroutine reads. The queue holds
    :data:`BUFFER_S` of audio: a reader that falls further behind loses the
    oldest blocks rather than sending the agent audio that is already stale.
    """

    __slots__ = ("_chunk_s", "_queue", "_stream")
    _stream: InputStreamHandle
    _queue: BlockQueue
    _chunk_s: float
    # How much audio may wait for the reader before the oldest block is dropped.
    BUFFER_S: ClassVar[float] = 1.0

    def __new__(
        cls, stream: InputStreamHandle, queue: BlockQueue, chunk_s: float
    ) -> Self:
        """Hold a started ``stream`` whose callback feeds ``queue``."""
        self = super().__new__(cls)
        self._stream, self._queue, self._chunk_s = stream, queue, chunk_s
        return self

    @classmethod
    def open(
        cls, backend: CaptureBackend, *, sample_rate_hz: int, chunk_s: float
    ) -> Self:
        """Query the default device and open a started stream on it, or raise.

        The backend owns opening and starting the stream, so a device that
        refuses either step is one :class:`OSError` and nothing is held.

        Raises:
            OSError: When the backend has no default input device, or the
                device refuses the stream.
        """
        block_frames = max(1, round(chunk_s * sample_rate_hz))
        device = backend.device()
        logger.info(  # The device names itself: its text may not forge a line.
            "Call input device: %s (default %.0f Hz), capturing at %d Hz",
            LogText.reason(device.name),
            device.default_samplerate_hz,
            sample_rate_hz,
        )
        loop = asyncio.get_running_loop()
        queue = BlockQueue(max(1, round(cls.BUFFER_S / chunk_s)))

        def _callback(
            indata: Buffer, _frames: int, _time: object, status: CallbackFlags
        ) -> None:
            if status.input_overflow:
                logger.warning("mic capture: input overflow (callback ran late)")
            loop.call_soon_threadsafe(queue.put, bytes(indata))

        stream = backend.open(
            samplerate=float(sample_rate_hz), blocksize=block_frames, callback=_callback
        )
        return cls(stream, queue, chunk_s)

    async def chunks(self) -> AsyncGenerator[AudioChunk]:
        """Yield one :class:`AudioChunk` per delivered block, until closed.

        Every chunk carries ``duration_s=chunk_s`` regardless of the block's
        wall-clock arrival: the block size is fixed and PortAudio delivers it
        at a steady cadence. A late callback (``status.input_overflow``) is
        logged, not measured into a corrected duration.

        Whatever the device captured before this reader started is discarded:
        the device is held from :meth:`open`, which may be well before the
        call is live, and that audio was never meant for the agent.
        """
        self._queue.discard()
        while True:
            pcm = await self._queue.get()
            yield AudioChunk(pcm=pcm, duration_s=self._chunk_s)

    def close(self) -> None:
        """Release the device; idempotent for a stream already closed."""
        self._stream.__exit__(None, None, None)
