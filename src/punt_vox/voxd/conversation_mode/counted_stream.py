"""A started input stream holding its device-table reservation until it is left."""

from __future__ import annotations

from typing import TYPE_CHECKING, Self, final

if TYPE_CHECKING:
    from punt_vox.voxd.conversation_mode.device_table import Reservation
    from punt_vox.voxd.conversation_mode.input_device import InputDevice
    from punt_vox.voxd.conversation_mode.mic_audio_source import NativeInputStream

__all__ = ["CountedStream"]


@final
class CountedStream:
    """A started stream holding its device-table reservation until it is left.

    The reservation was taken before the native stream was created; leaving
    the stream tears it down strictly through its device, then releases the
    reservation whatever the teardown did. A second leave does nothing.
    """

    __slots__ = ("_device", "_left", "_reservation", "_stream")
    _device: InputDevice
    _left: bool
    _reservation: Reservation
    _stream: NativeInputStream

    def __new__(
        cls, device: InputDevice, stream: NativeInputStream, reservation: Reservation
    ) -> Self:
        """Hold *stream*, opened on *device*, and the *reservation* taken for it."""
        self = super().__new__(cls)
        self._device, self._stream, self._reservation = device, stream, reservation
        self._left = False
        return self

    def __exit__(self, *exc_info: object) -> None:
        """Tear the stream down, then release its reservation; once only.

        Raises:
            OSError: When PortAudio refuses to stop or to close the stream
                (:meth:`InputDevice.released`); the reservation is released
                all the same.
        """
        if self._left:
            return
        self._left = True
        try:
            self._device.released(self._stream)
        finally:
            self._reservation.release()
