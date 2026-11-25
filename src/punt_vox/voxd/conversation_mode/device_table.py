"""PortAudio's device table, re-read before a stream opens when none is open.

PortAudio enumerates the system's audio devices once, in ``Pa_Initialize``,
and answers every later query -- including which device is the default --
from that copy. ``import sounddevice`` initializes it, so a long-running
voxd keeps the table it read at startup: a headset connected after that
is invisible to a call, which captures from the built-in microphone and
plays through the speakers while every fresh player process (music through
mpv) correctly follows the headset. The speakers then feed the microphone
and the agent interrupts itself.

The only way to re-read the table is ``Pa_Terminate`` then ``Pa_Initialize``,
and ``Pa_Terminate`` closes every stream still open. So the table is one
object per process, it counts the streams open on it, and :meth:`refresh`
re-reads only when that count is zero. A call opens its microphone first
and its output streams later, so one refresh at the microphone serves both
legs; a scripted call, with no microphone, refreshes at each output open.

A stream is counted from before its native stream exists: the opener takes
a :class:`Reservation` under the table's lock, then creates the stream, so
no refresh on another thread can terminate PortAudio between the stream's
creation and its count. The reservation is released once, when the stream
closes or when creating or starting it fails.
"""

from __future__ import annotations

import logging
import sys
import threading
from typing import ClassVar, Literal, Self, final

from punt_vox.voxd.log_text import LogText

__all__ = ["DeviceTable", "Reservation"]

logger = logging.getLogger(__name__)


@final
class DeviceTable:
    """The process-wide PortAudio device table and the streams open on it."""

    __slots__ = ("_initialized", "_lock", "_open_streams")
    _initialized: bool
    _lock: threading.Lock
    _open_streams: int
    _shared: ClassVar[DeviceTable | None] = None  # None: not asked for yet.

    def __new__(cls) -> Self:
        """Create a table with no stream open on it and PortAudio initialized.

        ``import sounddevice`` initializes PortAudio, and :meth:`refresh`
        does nothing before that import, so the library is initialized
        whenever the flag is first consulted.
        """
        self = super().__new__(cls)
        self._lock = threading.Lock()
        self._open_streams = 0
        self._initialized = True
        return self

    @classmethod
    def shared(cls) -> DeviceTable:
        """Return the one table this process's PortAudio library has."""
        if cls._shared is None:
            cls._shared = cls()
        return cls._shared

    @property
    def open_streams(self) -> int:
        """How many streams are open on the current table."""
        return self._open_streams

    @property
    def initialized(self) -> bool:
        """Whether PortAudio is initialized; a failed initialize leaves it not."""
        return self._initialized

    def refresh(self) -> bool:
        """Re-read the device table from the system; return whether it was.

        Nothing is done when ``sounddevice`` has not been imported yet --
        the import that follows will enumerate the devices as they are now,
        so there is no stale table to replace -- or when a stream is open,
        because terminating PortAudio would close it.

        PortAudio is terminated only while it is initialized. An initialize
        that fails leaves the library terminated, and a refresh that then
        terminated it again would fail at that step, every time, until the
        daemon restarted; instead the next refresh initializes directly.

        Raises:
            sounddevice.PortAudioError: When PortAudio refuses to terminate
                or to initialize again; the device the caller is about to
                open would fail in the same way.
        """
        with self._lock:
            if "sounddevice" not in sys.modules:
                return False
            if self._open_streams:
                logger.debug(
                    "PortAudio device table kept: %d stream(s) open",
                    self._open_streams,
                )
                return False
            # sounddevice exposes no public re-enumeration; these two wrap
            # Pa_Terminate and Pa_Initialize and are declared in the stub.
            from sounddevice import _initialize, _terminate

            if self._initialized:
                _terminate()
                self._initialized = False
            _initialize()
            self._initialized = True
            logger.info(
                "PortAudio device table re-read from the system: %s", self._summary()
            )
            return True

    @staticmethod
    def _summary() -> str:
        """Describe the table just read: device count and the default indices.

        The description is for the log and never fails the refresh. A missing
        default is reported as such; the open that follows raises the error
        the caller handles. Counting the devices makes ``sounddevice``
        describe every one of them, names included, and a device it cannot
        describe -- a name in an encoding it does not expect, an entry
        PortAudio will not report -- is unrelated to the call being placed,
        so the count is reported as unavailable instead; a default whose
        own name cannot be decoded is reported as undescribable.
        """
        from sounddevice import PortAudioError, query_devices

        def default(kind: Literal["input", "output"]) -> str:
            try:
                index = query_devices(kind=kind).get("index")
            except PortAudioError:
                return "none"
            except UnicodeDecodeError:  # a default name the library cannot decode
                return "undescribable"
            return "none" if index is None else f"#{index}"

        try:
            count = f"{len(query_devices())} devices"
        except (PortAudioError, UnicodeDecodeError) as exc:
            count = f"device count unavailable ({LogText.failure(exc)})"
        return (
            f"{count}, default input {default('input')}, "
            f"default output {default('output')}"
        )

    def reserve(self) -> Reservation:
        """Count one more stream on the current table, before it is created."""
        with self._lock:
            self._open_streams += 1
        return Reservation(self)

    def release(self) -> None:
        """Count one stream fewer; releasing more than were reserved is a bug.

        :class:`Reservation` calls this once per reservation; a caller holds
        the reservation, not the table, so the release cannot repeat.

        Raises:
            ValueError: When no stream is reserved.
        """
        with self._lock:
            if self._open_streams == 0:
                msg = "no PortAudio stream is reserved to release"
                raise ValueError(msg)
            self._open_streams -= 1


@final
class Reservation:
    """One stream's count on the table, from before it exists until released."""

    __slots__ = ("_held", "_table")
    _held: bool
    _table: DeviceTable

    def __new__(cls, table: DeviceTable) -> Self:
        """Hold the count *table* has just taken for one stream."""
        self = super().__new__(cls)
        self._table = table
        self._held = True
        return self

    @property
    def held(self) -> bool:
        """Whether the count is still held on the table."""
        return self._held

    def release(self) -> None:
        """Give the count back; a second release does nothing."""
        if self._held:
            self._held = False
            self._table.release()
