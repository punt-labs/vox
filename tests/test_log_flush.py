"""Tests for the background log flusher (src/punt_vox/log_flush.py)."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

from punt_vox.append_log import AtomicAppendLog
from punt_vox.client_errors import VoxdConnectionError
from punt_vox.log_flush import PeriodicFlusher
from punt_vox.log_ship import LogShipper
from punt_vox.log_wire import LogRecordWire

if TYPE_CHECKING:
    import pytest


class _StuckThread(threading.Thread):
    """A thread stand-in that is always alive and whose join never returns.

    Simulates a ``stop()`` whose bounded join times out on a still-running thread.
    """

    def __init__(self) -> None:
        super().__init__(daemon=True)

    def is_alive(self) -> bool:
        return True

    def join(self, timeout: float | None = None) -> None:
        return None


def _wire(message: str) -> LogRecordWire:
    return LogRecordWire(
        role="mcp", name="punt_vox.server", level="INFO", created=0.0, message=message
    )


class _DownClient:
    """A VoxClient stand-in whose connect always fails (daemon unreachable)."""

    async def connect(self) -> None:
        raise VoxdConnectionError("daemon down")

    async def close(self) -> None:
        return None


class _CloseRaisesClient:
    """A VoxClient stand-in that connects fine but raises on close.

    Exercises the exact finding: a per-cycle error after a successful connect
    must not propagate out of the daemon thread and end periodic shipping.
    """

    async def connect(self) -> None:
        return None

    async def close(self) -> None:
        raise OSError("socket close failed")


class _NonTransportRaisesClient:
    """A VoxClient stand-in whose connect raises a non-transport exception.

    The narrow guard only caught ``(OSError, RuntimeError, WebSocketException)``;
    a ``ValueError`` (a genuine bug or a monkeypatched ship path) slipped past it
    and killed the daemon thread. The broadened ``except Exception`` must catch it
    too, so any per-cycle error -- transport or not -- falls back and keeps looping.
    """

    async def connect(self) -> None:
        raise ValueError("unexpected non-transport failure")

    async def close(self) -> None:
        return None


class TestPeriodicFlusher:
    """The flusher drains the buffer periodically, or locally when voxd is down."""

    def test_daemon_down_drains_pending_to_fallback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        shipper = LogShipper(AtomicAppendLog(tmp_path / "fallback.log"))
        shipper.enqueue(_wire("buffered while idle"))
        monkeypatch.setattr(LogShipper, "_instance", shipper)
        monkeypatch.setattr("punt_vox.log_flush.VoxClient", _DownClient)

        flusher = PeriodicFlusher(interval=0.05)
        flusher.start()
        time.sleep(0.2)
        flusher.stop()

        contents = (tmp_path / "fallback.log").read_text(encoding="utf-8")
        assert contents.strip().endswith("buffered while idle")  # durable, not lost

    def test_start_is_idempotent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(LogShipper, "_instance", None)
        flusher = PeriodicFlusher(interval=0.05)
        flusher.start()
        flusher.start()  # second call must not spawn a second thread
        flusher.stop()

    def test_no_shipper_is_a_noop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A process that never installed a client handler flushes nothing."""
        monkeypatch.setattr(LogShipper, "_instance", None)
        flusher = PeriodicFlusher(interval=0.05)
        flusher.start()
        time.sleep(0.1)
        flusher.stop()  # no shipper -> no connection attempt, no crash

    def test_start_after_stop_flushes_periodically(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """start() after stop() must clear _stop so the thread keeps flushing.

        Without the clear, the fresh thread exits its first _stop.wait and a
        record enqueued after start() is never drained by the periodic loop.
        """
        shipper = LogShipper(AtomicAppendLog(tmp_path / "fallback.log"))
        monkeypatch.setattr(LogShipper, "_instance", shipper)
        monkeypatch.setattr("punt_vox.log_flush.VoxClient", _DownClient)

        flusher = PeriodicFlusher(interval=0.05)
        flusher.stop()  # arm _stop before any start (stop-before-start / restart)
        flusher.start()
        time.sleep(0.15)  # let the loop take a cycle
        shipper.enqueue(_wire("enqueued after restart"))
        time.sleep(0.15)  # the periodic loop must drain it (only if _stop cleared)
        drained_by_loop = not shipper.has_pending
        flusher.stop()

        assert drained_by_loop  # the thread kept running, not exited immediately

    def test_cycle_error_does_not_kill_the_thread(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A per-cycle error (close raising after connect) must not end the loop.

        The failing cycle falls back locally and the thread survives to flush the
        next cycle, so periodic shipping never silently stops.
        """
        fallback = tmp_path / "fallback.log"
        shipper = LogShipper(AtomicAppendLog(fallback))
        monkeypatch.setattr(LogShipper, "_instance", shipper)
        monkeypatch.setattr("punt_vox.log_flush.VoxClient", _CloseRaisesClient)

        shipper.enqueue(_wire("cycle one"))
        flusher = PeriodicFlusher(interval=0.05)
        flusher.start()
        time.sleep(0.15)  # first cycle raises on close -> falls back, thread survives
        shipper.enqueue(_wire("cycle two"))
        time.sleep(0.15)  # a live thread drains the second cycle too
        flusher.stop()

        text = fallback.read_text(encoding="utf-8")
        assert "cycle one" in text  # the error cycle still salvaged its batch
        assert "cycle two" in text  # the thread survived to flush again

    def test_non_transport_error_does_not_kill_the_thread(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A NON-transport per-cycle error must not end the loop either.

        The narrow guard caught only transport types; a ``ValueError`` (a real
        bug, or a monkeypatched ship path) slipped past and silently ended
        periodic shipping. The broadened ``except Exception`` falls back and the
        thread survives to flush the next cycle.
        """
        fallback = tmp_path / "fallback.log"
        shipper = LogShipper(AtomicAppendLog(fallback))
        monkeypatch.setattr(LogShipper, "_instance", shipper)
        monkeypatch.setattr("punt_vox.log_flush.VoxClient", _NonTransportRaisesClient)

        shipper.enqueue(_wire("cycle one"))
        flusher = PeriodicFlusher(interval=0.05)
        flusher.start()
        time.sleep(0.15)  # first cycle raises ValueError -> falls back, survives
        shipper.enqueue(_wire("cycle two"))
        time.sleep(0.15)  # a live thread drains the second cycle too
        flusher.stop()

        text = fallback.read_text(encoding="utf-8")
        assert "cycle one" in text  # the non-transport cycle salvaged its batch
        assert "cycle two" in text  # the thread survived to flush again

    def test_timed_out_stop_does_not_spawn_a_duplicate(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A stop() whose join times out keeps the live thread; start() no-ops.

        Clearing the handle after a timed-out join would let the next start()
        spawn a SECOND thread against the same buffer.
        """
        monkeypatch.setattr(LogShipper, "_instance", None)  # final drain is a no-op
        flusher = PeriodicFlusher(interval=0.05)
        stuck = _StuckThread()
        flusher._thread = stuck  # simulate a thread that outlives the join timeout

        flusher.stop()  # join times out (still alive) -> handle must be kept
        assert flusher._thread is stuck  # not cleared

        flusher.start()  # sees an alive thread -> no-op, no duplicate spawned
        assert flusher._thread is stuck
