"""Tests for the background log flusher (src/punt_vox/log_flush.py)."""

from __future__ import annotations

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
