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

from typing import TYPE_CHECKING, Self, final

from punt_vox.voxd.conversation_mode.counted_stream import CountedStream
from punt_vox.voxd.conversation_mode.device_pair import DevicePair
from punt_vox.voxd.conversation_mode.device_table import DeviceTable
from punt_vox.voxd.conversation_mode.input_device import InputDevice
from punt_vox.voxd.conversation_mode.output_device import OutputDevice

if TYPE_CHECKING:
    # Type-only: the name is declared in ``typings/sounddevice/__init__.pyi``
    # for the type checkers; the stub-free runtime module has no such attribute.
    from sounddevice import DeviceInfo, RawInputCallback

    from punt_vox.voxd.conversation_mode.mic_audio_source import InputStreamHandle

__all__ = ["PortAudio"]


@final
class PortAudio:
    """The system microphone through PortAudio's default input device.

    Every stream it opens is counted on the :class:`DeviceTable`, and
    :meth:`devices` re-reads that table first, so a call that starts after
    the person connects a headset finds the headset.
    """

    __slots__ = ("_table",)
    _table: DeviceTable

    def __new__(cls, table: DeviceTable | None = None) -> Self:  # None: the process's.
        """Create the backend over *table*, the process's shared one by default."""
        self = super().__new__(cls)
        self._table = table if table is not None else DeviceTable.shared()
        return self

    def devices(self) -> DevicePair:
        """Re-read the device table and report the current default devices.

        Raises:
            OSError: When PortAudio has no default input or output device,
                or reports one it cannot describe (:meth:`device`).
        """
        self._table.refresh()
        return DevicePair(self.device(), self.output_device())

    @staticmethod
    def device() -> InputDevice:
        """Ask PortAudio for the default input device, as its table stands.

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
    def output_device() -> OutputDevice:
        """Ask PortAudio for the default output device, as its table stands.

        Raises:
            OSError: When PortAudio has no default output device, or reports
                one with no name.
        """
        from sounddevice import PortAudioError, query_devices

        try:
            return PortAudio._reported_output(query_devices(kind="output"))
        except PortAudioError as exc:
            raise OSError(f"no default output device: {exc}") from exc

    @staticmethod
    def _reported_output(info: DeviceInfo) -> OutputDevice:
        """Build the output device PortAudio reported, refusing a nameless one.

        Raises:
            OSError: When the reported name is empty or the sample rate is
                not positive.
        """
        try:
            return OutputDevice(str(info["name"]), float(info["default_samplerate"]))
        except ValueError as exc:
            raise OSError(f"no usable default output device: {exc}") from exc

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

    def open(
        self,
        device: InputDevice,
        *,
        samplerate: float,
        blocksize: int,
        callback: RawInputCallback,
    ) -> InputStreamHandle:
        """Open and start a raw mono int16 stream on the default input device.

        ``sounddevice`` opens the native stream in the constructor and starts
        it on entry, and either step can fail with its own
        :class:`~sounddevice.PortAudioError`. Both are one refusal here: the
        caller sees the same :class:`OSError` naming *device*, and a stream
        that opened but would not start is closed before it is raised, so
        no device handle outlives the failure.

        *device* is the one :meth:`devices` reported, so the refusal names
        the device PortAudio was asked for even when a later query would
        fail (the device was unplugged or grabbed meanwhile). The started
        stream is counted on the table until it is left.

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
            raise device.refused(exc) from exc
        return CountedStream(device.started(stream), self._table)
