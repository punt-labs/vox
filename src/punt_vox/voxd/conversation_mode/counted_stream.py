"""A started input stream holding its device-table reservation until it is left."""

from __future__ import annotations

from typing import TYPE_CHECKING, Self, final

if TYPE_CHECKING:
    from punt_vox.voxd.conversation_mode.device_table import Reservation
    from punt_vox.voxd.conversation_mode.mic_audio_source import InputStreamHandle

__all__ = ["CountedStream"]


@final
class CountedStream:
    """A started stream holding its device-table reservation until it is left.

    The reservation was taken before the native stream was created; leaving
    the stream releases it, and a second leave releases nothing more.
    """

    __slots__ = ("_reservation", "_stream")
    _reservation: Reservation
    _stream: InputStreamHandle

    def __new__(cls, stream: InputStreamHandle, reservation: Reservation) -> Self:
        """Hold *stream* and the *reservation* taken for it."""
        self = super().__new__(cls)
        self._stream, self._reservation = stream, reservation
        return self

    def __exit__(self, *exc_info: object) -> None:
        """Leave the stream, then release its reservation; a second leave is a no-op."""
        try:
            self._stream.__exit__(*exc_info)
        finally:
            self._reservation.release()
