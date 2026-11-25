"""Output streams opened on the current device table and counted on it.

:class:`CountedOutput` wraps the daemon's output stream factory: before it
opens a stream it asks the :class:`~.device_table.DeviceTable` to re-read
PortAudio's devices (a no-op while any stream is open), so a scripted call
-- which has no microphone to do that at call start -- still plays through
the system's current default output. The stream it returns is counted on
the table from before its native stream is created to its close, so no
refresh can terminate PortAudio underneath a stream that exists but is not
yet counted.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Self, final

if TYPE_CHECKING:
    from collections.abc import Callable

    from punt_vox.voxd.conversation_mode.device_table import DeviceTable, Reservation
    from punt_vox.voxd.pcm_output import (
        OutputCallback,
        OutputStream,
        OutputStreamFactory,
    )

__all__ = ["CountedOutput", "CountedOutputStream"]


@final
class CountedOutputStream:
    """An output stream holding its device-table reservation until it closes."""

    __slots__ = ("_reservation", "_stream")
    _reservation: Reservation
    _stream: OutputStream

    def __new__(cls, stream: OutputStream, reservation: Reservation) -> Self:
        """Wrap an unstarted *stream* with the *reservation* taken before it existed."""
        self = super().__new__(cls)
        self._stream, self._reservation = stream, reservation
        return self

    def start(self) -> None:
        """Start the stream; one that will not start gives its reservation back.

        The caller still closes the stream, which releases nothing more.
        """
        try:
            self._stream.start()
        except BaseException:
            self._reservation.release()
            raise

    def stop(self) -> None:
        """Stop after the device has played every block already pulled."""
        self._stream.stop()

    def abort(self) -> None:
        """Stop at once, discarding what the device has not yet played."""
        self._stream.abort()

    def close(self) -> None:
        """Release the device, then its reservation; idempotent like the stream."""
        try:
            self._stream.close()
        finally:
            self._reservation.release()


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
        """Re-read the table when nothing is open, then open an unstarted stream.

        The reservation is taken before the factory runs, so a refresh on
        another thread finds the count raised from the moment the native
        stream can exist; a factory that fails gives it back.
        """
        self._table.refresh()
        reservation = self._table.reserve()
        try:
            stream = self._factory(
                samplerate=samplerate,
                blocksize=blocksize,
                callback=callback,
                finished_callback=finished_callback,
            )
        except BaseException:
            reservation.release()
            raise
        return CountedOutputStream(stream, reservation)
