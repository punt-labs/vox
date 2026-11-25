"""A started input stream counted on the device table until it is left."""

from __future__ import annotations

from typing import TYPE_CHECKING, Self, final

if TYPE_CHECKING:
    from punt_vox.voxd.conversation_mode.device_table import DeviceTable
    from punt_vox.voxd.conversation_mode.mic_audio_source import InputStreamHandle

__all__ = ["CountedStream"]


@final
class CountedStream:
    """A started stream counted on the device table until it is left."""

    __slots__ = ("_counted", "_stream", "_table")
    _counted: bool
    _stream: InputStreamHandle
    _table: DeviceTable

    def __new__(cls, stream: InputStreamHandle, table: DeviceTable) -> Self:
        """Count *stream* as open on *table*."""
        self = super().__new__(cls)
        self._stream, self._table = stream, table
        table.stream_opened()
        self._counted = True
        return self

    def __exit__(self, *exc_info: object) -> None:
        """Leave the stream, then release its count; idempotent like the stream."""
        try:
            self._stream.__exit__(*exc_info)
        finally:
            if self._counted:
                self._counted = False
                self._table.stream_closed()
