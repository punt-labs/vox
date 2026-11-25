"""Output streams opened on the current device table and counted on it.

:class:`CountedOutput` wraps the daemon's output stream factory: before it
opens a stream it asks the :class:`~.device_table.DeviceTable` to re-read
PortAudio's devices (a no-op while any stream is open), so a scripted call
-- which has no microphone to do that at call start -- still plays through
the system's current default output. The stream it returns is counted on
the table from a successful start to its close.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Self, final

if TYPE_CHECKING:
    from collections.abc import Callable

    from punt_vox.voxd.conversation_mode.device_table import DeviceTable
    from punt_vox.voxd.pcm_output import (
        OutputCallback,
        OutputStream,
        OutputStreamFactory,
    )

__all__ = ["CountedOutput", "CountedOutputStream"]


@final
class CountedOutputStream:
    """An output stream counted on the device table while it is started."""

    __slots__ = ("_counted", "_stream", "_table")
    _counted: bool
    _stream: OutputStream
    _table: DeviceTable

    def __new__(cls, stream: OutputStream, table: DeviceTable) -> Self:
        """Wrap an unstarted *stream*; it is counted once :meth:`start` succeeds."""
        self = super().__new__(cls)
        self._stream, self._table = stream, table
        self._counted = False
        return self

    def start(self) -> None:
        """Start the stream and count it on the table."""
        self._stream.start()
        self._table.stream_opened()
        self._counted = True

    def stop(self) -> None:
        """Stop after the device has played every block already pulled."""
        self._stream.stop()

    def abort(self) -> None:
        """Stop at once, discarding what the device has not yet played."""
        self._stream.abort()

    def close(self) -> None:
        """Release the device, then its count; idempotent like the stream."""
        try:
            self._stream.close()
        finally:
            if self._counted:
                self._counted = False
                self._table.stream_closed()


@final
class CountedOutput:
    """An output stream factory that re-reads the device table before opening."""

    __slots__ = ("_factory", "_table")
    _factory: OutputStreamFactory
    _table: DeviceTable

    def __new__(cls, factory: OutputStreamFactory, table: DeviceTable) -> Self:
        """Open streams with *factory* on *table*'s current devices."""
        self = super().__new__(cls)
        self._factory, self._table = factory, table
        return self

    def __call__(
        self,
        *,
        samplerate: int,
        blocksize: int,
        callback: OutputCallback,
        finished_callback: Callable[[], None],
    ) -> CountedOutputStream:
        """Re-read the table when nothing is open, then open an unstarted stream."""
        self._table.refresh()
        stream = self._factory(
            samplerate=samplerate,
            blocksize=blocksize,
            callback=callback,
            finished_callback=finished_callback,
        )
        return CountedOutputStream(stream, self._table)
