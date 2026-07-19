"""A background flusher that drains the client log buffer to voxd every few seconds.

Only the long-lived MCP server runs this. Short-lived clients (hooks, CLI, detached
playback) exit within milliseconds and flush on their own per-call connect/close, so
they never spawn a thread they would not use. The server, by contrast, may sit between
tool calls for minutes; without a periodic drain its buffered records would not become
durable until the next call or ``atexit``. This flusher closes that gap: a daemon
thread wakes every ``interval`` seconds and, if anything is buffered, opens a
throwaway connection whose handshake flushes the deque to ``vox.log``.

It never blocks the hot path (it runs on its own thread) and never loses records: when
voxd is unreachable it drains the buffer to the local fallback file, so the tail is
durable within seconds regardless of the daemon's state. On shutdown it stops and does
one final drain.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Self, final

from punt_vox.client import VoxClient
from punt_vox.client_errors import VoxdConnectionError
from punt_vox.log_ship import LogShipper

__all__ = ["PeriodicFlusher"]

_DEFAULT_INTERVAL_S = 2.0


@final
class PeriodicFlusher:
    """Drain the client log buffer to voxd on a background daemon thread."""

    __slots__ = ("_interval", "_stop", "_thread")

    _interval: float
    _stop: threading.Event
    _thread: threading.Thread | None

    def __new__(cls, *, interval: float = _DEFAULT_INTERVAL_S) -> Self:
        self = super().__new__(cls)
        self._interval = interval
        self._stop = threading.Event()
        self._thread = None
        return self

    def start(self) -> None:
        """Spawn the flush thread once; a second call is a no-op.

        Clear the stop flag first: a ``stop()`` before ``start()`` (or a
        stop/restart) leaves it set, and a fresh thread would then exit its very
        first ``_stop.wait`` immediately, never flushing.
        """
        # No-op only while a thread is actually alive; a dead handle (a prior
        # thread that exited, or a stop() whose join timed out then finished) is
        # replaced rather than left to block every future start().
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="vox-log-flusher", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """Signal the thread to exit, join it, and do one final drain.

        Clear the handle only if the join actually finished. A timed-out join
        leaves a still-alive thread, and clearing the handle then would let a
        later start() spawn a duplicate; keeping it lets start() no-op instead.
        """
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=self._interval + 1.0)
            if not thread.is_alive():  # cleared only on a completed join
                self._thread = None
        self._flush_once()

    def _run(self) -> None:
        """Flush every ``interval`` seconds until stopped, then flush once more."""
        while not self._stop.wait(self._interval):
            self._flush_once()
        self._flush_once()

    def _flush_once(self) -> None:
        """Ship the buffered records now, or drain them locally if voxd is down.

        A fresh event loop per cycle is fine at this cadence; the connection's
        handshake flushes the deque via the shipper, and a failed connect means
        the tail goes to the fallback file so it stays durable within seconds.

        Any per-cycle ``Exception`` -- a transport error (``client.close()``
        raising after a successful connect) or a genuine bug in the ship path --
        is caught and routed to the fallback, so ONE bad cycle can never kill the
        daemon thread and stop periodic shipping. ``KeyboardInterrupt``,
        ``SystemExit``, and ``CancelledError`` are not ``Exception`` subclasses,
        so they still propagate and let the process shut down. The fallback path
        uses raw ``os`` writes, so it cannot re-enter ``logging`` and recurse.
        """
        shipper = LogShipper.active()
        if shipper is None or not shipper.has_pending:
            return
        try:
            asyncio.run(self._ship(shipper))
        except Exception:  # noqa: BLE001 -- durability thread must never die on a cycle
            # Any per-cycle failure (broken close/socket after a successful
            # connect, or an unexpected bug in the ship path) must not kill the
            # thread -- route the batch to the raw-os fallback and keep looping.
            shipper.drain_to_fallback()

    @staticmethod
    async def _ship(shipper: LogShipper) -> None:
        """Open a throwaway connection (its handshake flushes) or fall back."""
        client = VoxClient()
        try:
            await client.connect()
        except VoxdConnectionError:
            shipper.drain_to_fallback()
            return
        finally:
            await client.close()
