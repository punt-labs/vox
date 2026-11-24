"""PortAudio as a capture backend: the default input device and a raw stream.

``sounddevice`` (PortAudio bindings) is the capture backend -- cross-platform
by construction, so there is no macOS/Linux dispatch here the way
``providers/say.py`` vs. ``providers/espeak.py`` need one for playback: both
platforms reach the same :class:`~sounddevice.RawInputStream` class, and
PortAudio itself resolves to CoreAudio or ALSA underneath. PortAudio's C
library is a separate runtime dependency from the Python package (Homebrew's
``portaudio`` on macOS, ``libportaudio2`` on most Linux distributions) --
the same shape as ``pydub``'s ffmpeg dependency, which this project already
accepts. Both methods import it on demand, so a host without it fails only
when a call opens the microphone.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, final

if TYPE_CHECKING:
    # Type-only: the name is declared in ``typings/sounddevice/__init__.pyi``
    # for the type checkers; the stub-free runtime module has no such attribute.
    from sounddevice import RawInputCallback

    from punt_vox.voxd.conversation_mode.mic_audio_source import InputStreamHandle

__all__ = ["InputDevice", "PortAudio"]


@dataclass(frozen=True, slots=True)
class InputDevice:
    """The input device PortAudio opens by default, as it reports itself."""

    name: str
    default_samplerate_hz: float


@final
class PortAudio:
    """The system microphone through PortAudio's default input device."""

    __slots__ = ()

    @staticmethod
    def device() -> InputDevice:
        """Ask PortAudio for the default input device.

        Raises:
            OSError: When PortAudio has no default input device.
        """
        from sounddevice import PortAudioError, query_devices

        try:
            info = query_devices(kind="input")
        except PortAudioError as exc:
            raise OSError(f"no default input device: {exc}") from exc
        return InputDevice(str(info["name"]), float(info["default_samplerate"]))

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

        Raises:
            OSError: When PortAudio refuses the stream: the device is busy,
                has no input channels, rejects the sample rate, or will not
                start.
        """
        from sounddevice import PortAudioError, RawInputStream

        try:
            stream = RawInputStream(
                samplerate=samplerate,
                blocksize=blocksize,
                channels=1,
                dtype="int16",
                callback=callback,
            )
        except PortAudioError as exc:
            raise PortAudio._refused(exc) from exc
        try:
            return stream.__enter__()
        except PortAudioError as exc:
            stream.close()
            raise PortAudio._refused(exc) from exc

    @staticmethod
    def _refused(exc: Exception) -> OSError:
        """Name the device PortAudio would not open or start, with a remedy."""
        device = PortAudio.device().name
        return OSError(
            f"cannot open input device {device}: {exc}. Close another "
            "application holding the microphone, or choose a device with "
            "an input channel"
        )
