"""Tests for the client log shipper and its fallback (src/punt_vox/log_ship.py)."""

from __future__ import annotations

import asyncio
import logging
import multiprocessing
import re
import threading
from pathlib import Path

from punt_vox.append_log import AtomicAppendLog
from punt_vox.log_ship import DaemonLogHandler, LogShipper
from punt_vox.log_wire import LogRecordWire

_TAIL = re.compile(r"proc=\d+ seq=\d+ [x]{30}$")


def _wire(message: str) -> LogRecordWire:
    return LogRecordWire(
        role="hook", name="punt_vox.hooks", level="INFO", created=0.0, message=message
    )


class _CollectingWs:
    """A fake WebSocket that records every frame it is asked to send."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, message: str) -> None:
        self.sent.append(message)


class _BrokenWs:
    """A fake WebSocket whose send always fails, forcing the fallback path."""

    async def send(self, message: str) -> None:
        raise ConnectionResetError(message)


class _YieldingWs:
    """A fake WebSocket whose send yields control, widening the drain race window."""

    def __init__(self) -> None:
        self.sent = 0

    async def send(self, message: str) -> None:
        assert message  # a frame was rendered
        self.sent += 1
        await asyncio.sleep(0)  # release the GIL mid-drain


def _fallback_many(path_str: str, proc_id: int, count: int) -> None:
    """Buffer *count* records then drain them to the shared fallback file."""
    shipper = LogShipper(AtomicAppendLog(Path(path_str)))
    for seq in range(count):
        shipper.enqueue(_wire(f"proc={proc_id} seq={seq} {'x' * 30}"))
    shipper.drain_to_fallback()


class TestLogShipper:
    """The shipper buffers records and ships them, or falls back locally."""

    def test_emit_buffers_and_flush_ships(self, tmp_path: Path) -> None:
        shipper = LogShipper(AtomicAppendLog(tmp_path / "fallback.log"))
        handler = DaemonLogHandler.bind(shipper, "hook")
        record = logging.LogRecord(
            "punt_vox.hooks", logging.INFO, __file__, 1, "played %d chimes", (3,), None
        )
        handler.emit(record)
        ws = _CollectingWs()
        asyncio.run(shipper.flush(ws))
        assert len(ws.sent) == 1
        assert "played 3 chimes" in ws.sent[0]
        assert not (tmp_path / "fallback.log").exists()  # shipped, nothing fell back

    def test_daemon_down_records_go_to_fallback(self, tmp_path: Path) -> None:
        fallback = tmp_path / "fallback.log"
        shipper = LogShipper(AtomicAppendLog(fallback))
        shipper.enqueue(_wire("first"))
        shipper.enqueue(_wire("second"))
        asyncio.run(shipper.flush(_BrokenWs()))
        lines = fallback.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2  # none lost
        assert lines[0].endswith("first")
        assert lines[1].endswith("second")
        assert (fallback.stat().st_mode & 0o077) == 0  # 0600

    def test_atexit_drains_to_fallback(self, tmp_path: Path) -> None:
        fallback = tmp_path / "fallback.log"
        shipper = LogShipper(AtomicAppendLog(fallback))
        shipper.enqueue(_wire("tail line"))
        shipper.drain_to_fallback()  # the atexit hook when a client never connected
        last = fallback.read_text(encoding="utf-8").splitlines()[-1]
        assert last.endswith("tail line")

    def test_deque_drops_oldest_and_counts(self, tmp_path: Path) -> None:
        fallback = tmp_path / "fallback.log"
        shipper = LogShipper(AtomicAppendLog(fallback))
        for i in range(1030):  # 6 past the 1024 bound
            shipper.enqueue(_wire(f"line {i}"))
        assert shipper.has_pending
        shipper.drain_to_fallback()
        text = fallback.read_text(encoding="utf-8")
        assert "log buffer overflowed: dropped 6 records" in text
        assert "line 0" not in text  # oldest evicted
        assert "line 1029" in text  # newest kept

    def test_flush_snapshot_does_not_livelock(self, tmp_path: Path) -> None:
        """A record enqueued during flush waits for the next flush, not this one."""
        shipper = LogShipper(AtomicAppendLog(tmp_path / "fallback.log"))
        shipper.enqueue(_wire("one"))

        class _ReentrantWs:
            def __init__(self) -> None:
                self.sent = 0

            async def send(self, message: str) -> None:
                assert message  # a frame was rendered
                self.sent += 1
                shipper.enqueue(_wire("added mid-flush"))

        ws = _ReentrantWs()
        asyncio.run(shipper.flush(ws))
        assert ws.sent == 1  # only the snapshot count, not the reentrant addition

    def test_concurrent_drainers_never_raise(self, tmp_path: Path) -> None:
        """Two threads draining the same shipper never pop an empty deque (H1).

        The D2 flusher thread and a tool-call worker thread both reach ``flush``;
        ``await ws.send`` releases the GIL. Without the drain lock the second
        thread empties the deque and the first pops empty -> IndexError out of the
        real tool call. The lock serializes them, so no drainer raises.
        """
        shipper = LogShipper(AtomicAppendLog(tmp_path / "fallback.log"))
        for i in range(500):
            shipper.enqueue(_wire(f"line {i}"))
        errors: list[BaseException] = []

        def _drain() -> None:
            try:
                asyncio.run(shipper.flush(_YieldingWs()))
            except (IndexError, RuntimeError, OSError) as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_drain) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        assert errors == []  # no IndexError from a concurrent drain
        assert not shipper.has_pending  # every buffered record was drained once

    def test_fallback_is_multiwriter_safe(self, tmp_path: Path) -> None:
        """Separate processes falling back to one file produce whole, intact lines."""
        path = tmp_path / "fallback.log"
        procs, per_proc = 5, 150
        ctx = multiprocessing.get_context("spawn")
        workers = [
            ctx.Process(target=_fallback_many, args=(str(path), pid, per_proc))
            for pid in range(procs)
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=60)
            assert worker.exitcode == 0
        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == procs * per_proc
        assert all(_TAIL.search(line) for line in lines)
