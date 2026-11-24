"""Live microphone capture, yielding :class:`AudioChunk` at a fixed cadence.

The backend that names the device and opens the stream is injected; the
production one is :class:`~.input_device.PortAudio`. :meth:`MicAudioSource.open`
holds the device from the moment it returns, so a caller can prove the
microphone works before doing anything that depends on it; the returned
:class:`~.mic_capture.MicCapture` yields the chunks and releases the device.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, Self, final, runtime_checkable

from punt_vox.voxd.conversation_mode.audio_chunk import SAMPLE_RATE_HZ
from punt_vox.voxd.conversation_mode.mic_capture import MicCapture

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    # Type-only: the name exists only in ``typings/sounddevice/__init__.pyi``
    # for the type checkers; importing it at runtime would raise ImportError.
    from sounddevice import RawInputCallback

    from punt_vox.voxd.conversation_mode.audio_chunk import AudioChunk
    from punt_vox.voxd.conversation_mode.input_device import InputDevice

__all__ = ["MicAudioSource"]

# Match the voice call transport's 20 ms audio frames.
_CHUNK_S = 0.02


@runtime_checkable
class InputStreamHandle(Protocol):
    """The context-manager surface a capture needs from a stream."""

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
    """The system microphone at a sample rate and chunk cadence."""

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

    def open(self) -> MicCapture:
        """Open the device now and return the capture that streams from it.

        Raises:
            OSError: When the backend has no default input device, or the
                device refuses the stream.
        """
        return MicCapture.open(
            self._backend, sample_rate_hz=self._sample_rate_hz, chunk_s=self._chunk_s
        )

    async def chunks(self) -> AsyncGenerator[AudioChunk]:
        """Open the device on first use and yield chunks until closed.

        Closing the async generator releases the device.

        Raises:
            OSError: As :meth:`open`.
        """
        capture = MicCapture.open(
            self._backend, sample_rate_hz=self._sample_rate_hz, chunk_s=self._chunk_s
        )
        try:
            async for chunk in capture.chunks():
                yield chunk
        finally:
            capture.close()
