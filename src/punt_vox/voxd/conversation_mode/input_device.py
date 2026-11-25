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

    from punt_vox.voxd.conversation_mode.mic_audio_source import NativeInputStream

__all__ = ["InputDevice"]

logger = logging.getLogger(__name__)


@final
class InputDevice:
    """The input device PortAudio opens by default, as it reports itself."""

    __slots__ = ("_default_samplerate_hz", "_index", "_name")

    _name: str
    _default_samplerate_hz: float
    _index: int | None  # None: described by name alone; opens the current default.

    def __new__(
        cls,
        name: str,
        default_samplerate_hz: float,
        *,
        index: int | None = None,  # None: no table index known for this device.
    ) -> Self:
        """Build a device from the name, sample rate and index PortAudio reports.

        Raises:
            ValueError: When the name is empty or the sample rate is not positive.
        """
        self = super().__new__(cls)
        self._name = cls._named(name)
        self._default_samplerate_hz = cls._positive_rate(default_samplerate_hz)
        self._index = index
        return self

    @property
    def name(self) -> str:
        """The device name as PortAudio reports it."""
        return self._name

    @property
    def default_samplerate_hz(self) -> float:
        """The device's default sample rate, in hertz."""
        return self._default_samplerate_hz

    @property
    def index(self) -> int | None:
        """PortAudio's index for this device, or None when described by name alone."""
        return self._index

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, InputDevice):
            return NotImplemented
        return self._key() == other._key()

    def __hash__(self) -> int:
        return hash((InputDevice, *self._key()))

    def __repr__(self) -> str:
        index = "" if self._index is None else f", index={self._index!r}"
        return f"InputDevice({self._name!r}, {self._default_samplerate_hz!r}{index})"

    def opened(
        self, *, samplerate: float, blocksize: int, callback: RawInputCallback
    ) -> NativeInputStream:
        """Create and start a raw mono int16 stream on this device, or refuse.

        A device with an index is opened by that index, so the stream is
        bound to this device and not to whatever the table calls the
        default by the time it opens. A table re-read in between can give
        the index to another device; the name at the index is checked
        after the open and a mismatch is a refusal naming both, with the
        stream closed. A device without an index opens the current default.

        ``sounddevice`` opens the native stream in the constructor and starts
        it on entry, and either step can fail with its own
        :class:`~sounddevice.PortAudioError`. Both are one refusal here: the
        caller sees the same :class:`OSError` naming this device, and a
        stream that opened but would not start is closed before it is
        raised, so no device handle outlives the failure.

        Raises:
            OSError: When PortAudio refuses the stream: the device is busy,
                has no input channels, rejects the sample rate, will not
                start, or its index now names another device.
        """
        from sounddevice import PortAudioError, RawInputStream

        bound = {} if self._index is None else {"device": self._index}
        try:
            stream = RawInputStream(
                samplerate=samplerate,
                blocksize=blocksize,
                channels=1,
                dtype="int16",
                callback=callback,
                **bound,
            )
        except PortAudioError as exc:
            raise self.refused(exc) from exc
        self._still_named(stream)
        return self.started(stream)

    def _still_named(self, stream: RawInputStream) -> None:
        """Refuse a stream whose index the table has given to another device.

        Identity is the name and the default sample rate together, so a
        same-named device at another rate is another device.

        Raises:
            OSError: When the device at this index now differs in name or
                rate, or cannot be described.
        """
        from sounddevice import PortAudioError, query_devices

        if self._index is None:
            return
        try:
            info = query_devices(self._index)
            now = InputDevice(str(info["name"]), float(info["default_samplerate"]))
        except (PortAudioError, UnicodeDecodeError, ValueError) as exc:
            self._close_unstarted(stream)
            raise self.refused(exc) from exc
        if now != self:
            self._close_unstarted(stream)
            raise self.refused(
                f"device {self._index} is now {now.name} at"
                f" {now.default_samplerate_hz} Hz, not {self._name} at"
                f" {self._default_samplerate_hz} Hz"
            )

    def started(self, stream: RawInputStream) -> NativeInputStream:
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

    def released(self, stream: NativeInputStream) -> None:
        """Stop and close *stream* strictly: the first teardown error is raised.

        ``sounddevice`` would hide both errors; here the stop's error does
        not skip the close, and whichever failed first names this device.

        Raises:
            OSError: When PortAudio refuses to stop or to close the stream.
        """
        from sounddevice import PortAudioError

        first: PortAudioError | None = None  # None: the stop succeeded.
        try:
            stream.stop(ignore_errors=False)
        except PortAudioError as exc:
            first = exc
        try:
            stream.close(ignore_errors=False)
        except PortAudioError as exc:
            first = first if first is not None else exc
        if first is not None:
            raise OSError(
                f"cannot release input device {self._name}: {first}"
            ) from first

    def refused(self, cause: Exception | str) -> OSError:
        """Name this device as the one PortAudio refused, with a remedy."""
        return OSError(
            f"cannot open input device {self._name}: {cause}. Close another "
            "application holding the microphone, or choose a device with an "
            "input channel"
        )

    def _key(self) -> tuple[str, float]:
        # The index is how the device is opened, not what it is: the same
        # device keeps its identity across a table re-read that renumbers it.
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
