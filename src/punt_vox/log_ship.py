"""Ship client log records to ``voxd`` over its WebSocket; fall back to a local file.

Every client process (MCP server, hook, CLI, detached playback) installs a
:class:`DaemonLogHandler` on the root logger. ``emit`` never connects and never
blocks: it captures the record as a :class:`LogRecordWire` and appends it to a
bounded deque owned by the module-singleton :class:`LogShipper`. CPython
``deque.append``/``popleft`` are atomic, so ``emit`` needs no drain lock.

Draining is a different matter. The long-lived server has *two* concurrent
drainers -- the background flusher on its own thread and a tool call's
worker-thread connection -- so every drain (``flush``, ``drain_to_fallback``,
their shared helpers, and the overflow-marker read/reset) is serialized by one
``threading.Lock``. Without it, two threads snapshot ``len(buffer)`` and then
race ``popleft`` -- ``await ws.send`` releases the GIL, the other thread empties
the deque, and the stale count pops an empty deque, raising out of ``connect``
and failing the real tool call. Each drain stays a *bounded* ``range(len(...))``
snapshot so a record re-enqueued mid-drain (the fallback sink's own chmod-failure
DEBUG line) waits for the next drain instead of live-locking this one.

When the daemon is unreachable -- a failed connect, a raising send, or a client
that never connects -- the buffered records are written to ``vox-fallback.log``
via :class:`AtomicAppendLog`, whose error path never re-enters ``logging`` (that
would recurse straight back here).
"""

from __future__ import annotations

import atexit
import json
import logging
import threading
from collections import deque
from typing import ClassVar, Protocol, Self, final

import websockets.exceptions

from punt_vox.append_log import AtomicAppendLog
from punt_vox.log_wire import LogRecordWire
from punt_vox.paths import log_dir

__all__ = ["DaemonLogHandler", "LogShipper", "WsSender"]

_FALLBACK_NAME = "vox-fallback.log"
_MAX_BUFFERED = 1024


class WsSender(Protocol):
    """The one WebSocket capability the shipper needs: send a text frame."""

    async def send(self, message: str) -> None:
        """Send *message* as a single text frame."""
        ...


@final
class LogShipper:
    """A bounded client→daemon log buffer with a local multi-writer fallback."""

    _instance: ClassVar[LogShipper | None] = None

    __slots__ = ("_buffer", "_count_lock", "_drain_lock", "_dropped", "_fallback")

    _buffer: deque[LogRecordWire]
    _fallback: AtomicAppendLog
    _dropped: int
    _drain_lock: threading.Lock
    _count_lock: threading.Lock

    def __new__(cls, fallback: AtomicAppendLog) -> Self:
        self = super().__new__(cls)
        self._buffer = deque(maxlen=_MAX_BUFFERED)
        self._fallback = fallback
        self._dropped = 0
        self._drain_lock = threading.Lock()
        self._count_lock = threading.Lock()
        return self

    @classmethod
    def build_handler(cls, *, role: str) -> DaemonLogHandler:
        """dictConfig ``()`` factory: return a handler bound to the shared singleton.

        The singleton persists across repeated ``configure_client_logging`` calls
        (the CLI configures on both the callback and the command), so a buffered
        record is never lost when the root handlers are rebuilt. Registers the
        ``atexit`` fallback drain exactly once.
        """
        if cls._instance is None:
            cls._instance = LogShipper(AtomicAppendLog(log_dir() / _FALLBACK_NAME))
            atexit.register(cls._instance.drain_to_fallback)
        return DaemonLogHandler.bind(cls._instance, role)

    @classmethod
    def active(cls) -> LogShipper | None:
        """Return the installed singleton, or ``None`` when no client configured one.

        The transport calls this on connect/close: a process that never installed
        a client log handler (a bare ``VoxClient`` in a test) simply has nothing
        to flush.
        """
        return cls._instance

    @property
    def has_pending(self) -> bool:
        """Return whether any record is waiting to ship or to be marked dropped."""
        return bool(self._buffer) or self._dropped > 0

    def enqueue(self, wire: LogRecordWire) -> None:
        """Append *wire*, counting an eviction when the bounded buffer is full.

        Lock-free on the hot path except the microscopic drop-count RMW, which
        takes ``_count_lock`` (never the drain lock, never held across I/O) so a
        concurrent drain can't deadlock a re-entrant enqueue.
        """
        if len(self._buffer) == _MAX_BUFFERED:
            with self._count_lock:
                self._dropped += 1
        self._buffer.append(wire)

    async def flush(self, ws: WsSender) -> None:
        """Send every buffered frame over *ws*; on failure route the rest to fallback.

        Serialized against every other drainer by ``_drain_lock`` (held across the
        ``await``, since drainers run on separate threads' own loops). Drains a
        bounded snapshot so records enqueued *during* the flush wait for the next
        one. A send failure means the socket is broken -- the following real RPC
        surfaces that -- so the in-flight batch goes to the fallback file and this
        returns without raising.
        """
        with self._drain_lock:
            self._drain_overflow_marker()
            for _ in range(len(self._buffer)):
                wire = self._buffer.popleft()
                try:
                    await ws.send(json.dumps(wire.to_wire()))
                except (
                    OSError,
                    RuntimeError,
                    websockets.exceptions.WebSocketException,
                ):
                    # Exhaustive by construction: the payload is always a str, so
                    # no TypeError/ValueError path exists; only a broken/closed
                    # socket raises here. The in-flight batch falls back, never
                    # raising into the real RPC that follows.
                    self._fallback.append(wire.format_line())
                    self._drain_remaining_to_fallback()
                    return

    def drain_to_fallback(self) -> None:
        """Write every buffered record to the fallback file (atexit / daemon-down)."""
        with self._drain_lock:
            self._drain_overflow_marker()
            self._drain_remaining_to_fallback()

    def _drain_remaining_to_fallback(self) -> None:
        """Append a bounded snapshot of buffered records to the fallback file.

        Caller holds ``_drain_lock``. Bounded (not ``while self._buffer``) so the
        fallback sink's own chmod-failure DEBUG line -- which re-enters ``enqueue``
        -- can't grow the deque faster than this drains it.
        """
        for _ in range(len(self._buffer)):
            self._fallback.append(self._buffer.popleft().format_line())

    def _drain_overflow_marker(self) -> None:
        """Record any dropped-record count as one fallback line, then reset it.

        Caller holds ``_drain_lock``. The count is read+reset under ``_count_lock``
        (released before the fallback write, which may re-enter ``enqueue``), and
        the marker goes to the fallback file -- never through ``logging`` -- so a
        lossy burst is traceable without recursing into this handler.
        """
        with self._count_lock:
            count = self._dropped
            self._dropped = 0
        if count:
            self._fallback.append(f"log buffer overflowed: dropped {count} records")


@final
class DaemonLogHandler(logging.Handler):
    """Root handler that hands each record to the :class:`LogShipper` buffer."""

    _shipper: LogShipper
    _role: str

    @classmethod
    def bind(cls, shipper: LogShipper, role: str) -> Self:
        """Build a handler bound to *shipper*, stamping shipped frames with *role*.

        A factory, not a constructor override: ``logging.Handler.__init__`` owns
        the handler machinery (level, lock, filters), so the shipper and role are
        attached after the parent initialises rather than fighting its signature.
        """
        handler = cls()
        handler._shipper = shipper
        handler._role = role
        return handler

    def emit(self, record: logging.LogRecord) -> None:
        """Capture *record* as a wire frame and buffer it; never raise, never log.

        The only realistic failure is ``getMessage`` mis-formatting a record's
        ``%`` args; ``handleError`` is logging's own last-resort sink for it, so a
        malformed record never crashes the caller that logged it.
        """
        try:
            self._shipper.enqueue(LogRecordWire.from_record(record, role=self._role))
        except (TypeError, ValueError):
            self.handleError(record)
