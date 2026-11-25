"""The input device PortAudio reports, and the stream it refuses or starts.

The device names itself in every refusal: a stream PortAudio would not open
or start on it raises one :class:`OSError` carrying its name, the error the
launcher releases the call ID on. The methods import ``sounddevice`` on
demand, so a host without the PortAudio runtime fails only when a call opens
the microphone (see :mod:`.portaudio`).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Self, final

from punt_vox.voxd.log_text import LogText

if TYPE_CHECKING:
    # Type-only: the name is declared in ``typings/sounddevice/__init__.pyi``
    # for the type checkers; the stub-free runtime module has no such attribute.
    from sounddevice import RawInputCallback, RawInputStream

    from punt_vox.voxd.conversation_mode.mic_audio_source import InputStreamHandle

__all__ = ["InputDevice"]

logger = logging.getLogger(__name__)


@final
class InputDevice:
    """The input device PortAudio opens by default, as it reports itself."""

    __slots__ = ("_default_samplerate_hz", "_name")

    _name: str
    _default_samplerate_hz: float

    def __new__(cls, name: str, default_samplerate_hz: float) -> Self:
        """Build a device from the name and default sample rate PortAudio reports.

        Raises:
            ValueError: When the name is empty or the sample rate is not positive.
        """
        self = super().__new__(cls)
        self._name = cls._named(name)
        self._default_samplerate_hz = cls._positive_rate(default_samplerate_hz)
        return self

    @property
    def name(self) -> str:
        """The device name as PortAudio reports it."""
        return self._name

    @property
    def default_samplerate_hz(self) -> float:
        """The device's default sample rate, in hertz."""
        return self._default_samplerate_hz

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, InputDevice):
            return NotImplemented
        return self._key() == other._key()

    def __hash__(self) -> int:
        return hash((InputDevice, *self._key()))

    def __repr__(self) -> str:
        return f"InputDevice({self._name!r}, {self._default_samplerate_hz!r})"

    def opened(
        self, *, samplerate: float, blocksize: int, callback: RawInputCallback
    ) -> InputStreamHandle:
        """Create and start a raw mono int16 stream on this device, or refuse.

        ``sounddevice`` opens the native stream in the constructor and starts
        it on entry, and either step can fail with its own
        :class:`~sounddevice.PortAudioError`. Both are one refusal here: the
        caller sees the same :class:`OSError` naming this device, and a
        stream that opened but would not start is closed before it is
        raised, so no device handle outlives the failure.

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
            raise self.refused(exc) from exc
        return self.started(stream)

    def started(self, stream: RawInputStream) -> InputStreamHandle:
        """Start a stream opened on this device; one that will not start is closed.

        Raises:
            OSError: When PortAudio will not start the stream.
        """
        from sounddevice import PortAudioError

        try:
            return stream.__enter__()
        except PortAudioError as exc:
            self._close_unstarted(stream)
            raise self.refused(exc) from exc

    def refused(self, exc: Exception) -> OSError:
        """Name this device as the one PortAudio refused, with a remedy."""
        return OSError(
            f"cannot open input device {self._name}: {exc}. Close another application "
            "holding the microphone, or choose a device with an input channel"
        )

    def _key(self) -> tuple[str, float]:
        return (self._name, self._default_samplerate_hz)

    @staticmethod
    def _named(name: str) -> str:
        if not name:
            msg = "input device name must not be empty"
            raise ValueError(msg)
        return name

    @staticmethod
    def _positive_rate(default_samplerate_hz: float) -> float:
        if not default_samplerate_hz > 0:  # Written so NaN fails the check too.
            msg = f"input device sample rate must be positive: {default_samplerate_hz}"
            raise ValueError(msg)
        return default_samplerate_hz

    def _close_unstarted(self, stream: RawInputStream) -> None:
        """Release a stream that would not start; a close that fails is logged.

        The start failure is the error the caller must see. A second
        PortAudio error, from the close, would replace it, so that one is a
        warning only, and it names the device whose stream stayed open.
        """
        from sounddevice import PortAudioError

        try:
            stream.close()
        except PortAudioError as exc:
            logger.warning(
                "Input stream on %s not closed after a start failure (%s)",
                self._name,
                LogText.failure(exc),
            )
