"""Live microphone capture, yielding :class:`AudioChunk` at a fixed cadence.

The backend that names the device and opens the stream is injected; the
production one is :class:`~.portaudio.PortAudio`. :meth:`MicAudioSource.open`
holds the device from the moment it returns, so a caller can prove the
microphone works before doing anything that depends on it; the returned
:class:`~.mic_capture.MicCapture` yields the chunks and releases the device.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, Self, final, runtime_checkable

from punt_vox.voxd.conversation_mode.audio_chunk import SAMPLE_RATE_HZ
from punt_vox.voxd.conversation_mode.mic_capture import MicCapture
from punt_vox.voxd.conversation_mode.portaudio import PortAudio

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    # Type-only: the name exists only in ``typings/sounddevice/__init__.pyi``
    # for the type checkers; importing it at runtime would raise ImportError.
    from sounddevice import RawInputCallback

    from punt_vox.voxd.conversation_mode.audio_chunk import AudioChunk
    from punt_vox.voxd.conversation_mode.device_pair import DevicePair
    from punt_vox.voxd.conversation_mode.input_device import InputDevice

__all__ = ["MicAudioSource"]

# Match the voice call transport's 20 ms audio frames.
_CHUNK_S = 0.02


@runtime_checkable
class InputStreamHandle(Protocol):
    """A started stream: leaving it stops the capture and releases the device."""

    def __exit__(self, *exc_info: object) -> None:
        """Stop the capture and release the device."""
        ...


class NativeInputStream(InputStreamHandle, Protocol):
    """The library's own started stream, whose teardown can be asked to fail loudly.

    ``sounddevice`` stops and closes with errors ignored by default; the
    adapter that counts the stream on the device table asks for them.
    """

    def stop(self, *, ignore_errors: bool = True) -> None:
        """Stop the stream; with *ignore_errors* off, PortAudio's refusal is raised."""
        ...

    def close(self, *, ignore_errors: bool = True) -> None:
        """Close the stream; with *ignore_errors* off, PortAudio's refusal is raised."""
        ...


class CaptureBackend(Protocol):
    """Names the current default devices and opens a started raw input stream.

    :meth:`devices` reports the system's defaults as they are now, not as
    they were when the process started; :meth:`open` takes the input device
    it reported, so a refusal names that device.
    """

    def devices(self) -> DevicePair:
        """Return the system's current default input and output devices."""
        ...

    def open(
        self,
        device: InputDevice,
        *,
        samplerate: float,
        blocksize: int,
        callback: RawInputCallback,
    ) -> InputStreamHandle:
        """Open and start a raw mono int16 stream on *device*, or raise."""
        ...


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
        """Create a mic audio source over ``backend``.

        Raises:
            ValueError: When ``chunk_s`` is not positive: a zero-length chunk
                would never advance the silence probe.
        """
        if chunk_s <= 0:
            raise ValueError(f"chunk_s must be positive, not {chunk_s}")
        self = super().__new__(cls)
        self._backend = backend
        self._sample_rate_hz = sample_rate_hz
        self._chunk_s = chunk_s
        return self

    @classmethod
    def with_portaudio(cls) -> Self:
        """Create the production source: the system microphone through PortAudio."""
        return cls(PortAudio())

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
