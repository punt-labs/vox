"""PortAudio as a capture backend: the default input device and a raw stream.

``sounddevice`` (PortAudio bindings) is the capture backend -- cross-platform
by construction, so there is no macOS/Linux dispatch here the way
``providers/say.py`` vs. ``providers/espeak.py`` need one for playback: both
platforms reach the same :class:`~sounddevice.RawInputStream` class, and
PortAudio itself resolves to CoreAudio or ALSA underneath. PortAudio's C
library is a separate runtime dependency from the Python package (Homebrew's
``portaudio`` on macOS, ``libportaudio2`` on most Linux distributions) --
the same shape as ``pydub``'s ffmpeg dependency, which this project already
accepts. Every method imports it on demand, so a host without it fails only
when a call opens the microphone.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, final

from punt_vox.voxd.conversation_mode.input_device import InputDevice

if TYPE_CHECKING:
    # Type-only: the name is declared in ``typings/sounddevice/__init__.pyi``
    # for the type checkers; the stub-free runtime module has no such attribute.
    from sounddevice import DeviceInfo, RawInputCallback

    from punt_vox.voxd.conversation_mode.mic_audio_source import InputStreamHandle

__all__ = ["PortAudio"]


@final
class PortAudio:
    """The system microphone through PortAudio's default input device."""

    __slots__ = ()

    @staticmethod
    def device() -> InputDevice:
        """Ask PortAudio for the default input device.

        A device PortAudio reports with no name or a sample rate that is not
        positive is as unusable as no device: the caller releases the call
        ID on an :class:`OSError`, so the :class:`ValueError` the device's
        validation raises is converted here rather than escaping as one.

        Raises:
            OSError: When PortAudio has no default input device, or reports
                one with no name or a non-positive sample rate.
        """
        from sounddevice import PortAudioError, query_devices

        try:
            return PortAudio._reported(query_devices(kind="input"))
        except PortAudioError as exc:
            raise OSError(f"no default input device: {exc}") from exc

    @staticmethod
    def _reported(info: DeviceInfo) -> InputDevice:
        """Build the device PortAudio reported, refusing one it cannot describe.

        Raises:
            OSError: When the reported name is empty or the sample rate is
                not positive.
        """
        try:
            return InputDevice(str(info["name"]), float(info["default_samplerate"]))
        except ValueError as exc:
            raise OSError(f"no usable default input device: {exc}") from exc

    @staticmethod
    def open(
        *, samplerate: float, blocksize: int, callback: RawInputCallback
    ) -> InputStreamHandle:
        """Open and start a raw mono int16 stream on the default input device.

        ``sounddevice`` opens the native stream in the constructor and starts
        it on entry, and either step can fail with its own
        :class:`~sounddevice.PortAudioError`. Both are one refusal here: the
        caller sees the same device-naming :class:`OSError`, and a stream
        that opened but would not start is closed before it is raised, so
        no device handle outlives the failure.

        The device is named before the stream is opened: a device that was
        unplugged or grabbed is exactly one a later query would fail on,
        and that failure would replace the open or start error the caller
        must see.

        Raises:
            OSError: When PortAudio has no default input device, or refuses
                the stream: the device is busy, has no input channels,
                rejects the sample rate, or will not start.
        """
        from sounddevice import PortAudioError, RawInputStream

        device = PortAudio.device()
        try:
            stream = RawInputStream(
                samplerate=samplerate,
                blocksize=blocksize,
                channels=1,
                dtype="int16",
                callback=callback,
            )
        except PortAudioError as exc:
            raise device.refused(exc) from exc
        return device.started(stream)
