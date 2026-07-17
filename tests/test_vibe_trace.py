"""Tests for the durable vibe-trace sink (src/punt_vox/vibe_trace.py)."""

from __future__ import annotations

import multiprocessing
import os
import re
from collections.abc import Buffer
from pathlib import Path
from typing import TYPE_CHECKING

from punt_vox.vibe_trace import VibeTraceLog

if TYPE_CHECKING:
    import pytest

_LINE = re.compile(r"^\[vibe-trace\] proc=\d+ seq=\d+ [x]{40}$")


def _append_many(path_str: str, proc_id: int, count: int) -> None:
    """Append *count* fixed-shape trace lines -- a spawn-safe multiprocess worker.

    Each line is a single ``proc``/``seq`` record padded to a stable width, so a
    torn or interleaved write shows up as a line that fails :data:`_LINE`.
    """
    trace = VibeTraceLog(Path(path_str))
    for seq in range(count):
        trace.record(f"proc={proc_id} seq={seq} {'x' * 40}")


class TestVibeTraceLog:
    """The sink appends greppable ``[vibe-trace]`` lines to a durable file."""

    def test_default_path_lives_under_state_logs(self) -> None:
        assert VibeTraceLog.default().path.name == "vibe-trace.log"
        assert VibeTraceLog.default().path.parent.name == "logs"

    def test_record_appends_prefixed_line(self, tmp_path: Path) -> None:
        trace = VibeTraceLog(tmp_path / "vibe-trace.log")
        trace.record("vibe set mood=relaxing music_playing=true hint_emitted=true")
        assert trace.path.read_text(encoding="utf-8") == (
            "[vibe-trace] vibe set mood=relaxing music_playing=true hint_emitted=true\n"
        )

    def test_record_appends_without_truncating(self, tmp_path: Path) -> None:
        trace = VibeTraceLog(tmp_path / "vibe-trace.log")
        trace.record("nudge fired counter=5->0 mode=auto")
        trace.record("music off")
        lines = trace.path.read_text(encoding="utf-8").splitlines()
        assert lines == [
            "[vibe-trace] nudge fired counter=5->0 mode=auto",
            "[vibe-trace] music off",
        ]

    def test_record_creates_missing_parent_dir(self, tmp_path: Path) -> None:
        trace = VibeTraceLog(tmp_path / "logs" / "nested" / "vibe-trace.log")
        trace.record("music off")
        assert trace.path.is_file()

    def test_record_file_is_private(self, tmp_path: Path) -> None:
        trace = VibeTraceLog(tmp_path / "vibe-trace.log")
        trace.record("music off")
        assert (trace.path.stat().st_mode & 0o077) == 0

    def test_record_completes_line_despite_short_write(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A short ``os.write`` still lands the whole line -- the drain loop re-issues.

        ``os.write`` may write fewer bytes than requested without raising (an
        ``ENOSPC`` short write). Writing a fragment and stopping would leave a
        newline-less tear; the loop must re-issue the remainder.
        """
        real_write = os.write
        calls = {"n": 0}

        def short_once(fd: int, data: Buffer) -> int:
            calls["n"] += 1
            if calls["n"] == 1:
                return real_write(fd, bytes(data)[:5])  # only the first 5 bytes
            return real_write(fd, data)

        monkeypatch.setattr("punt_vox.vibe_trace.os.write", short_once)
        trace = VibeTraceLog(tmp_path / "vibe-trace.log")
        trace.record("music off")

        assert calls["n"] >= 2  # the first write was short, so the loop re-issued
        assert trace.path.read_text(encoding="utf-8") == "[vibe-trace] music off\n"

    def test_is_writable_true_for_writable_dir(self, tmp_path: Path) -> None:
        """An absent log under a writable ancestor reports writable."""
        assert VibeTraceLog(tmp_path / "vibe-trace.log").is_writable() is True

    def test_is_writable_false_when_dir_unwritable(self, tmp_path: Path) -> None:
        """A read-only ancestor -- one the log could never be created in -- is not."""
        locked = tmp_path / "locked"
        locked.mkdir()
        locked.chmod(0o500)
        try:
            assert VibeTraceLog(locked / "vibe-trace.log").is_writable() is False
        finally:
            locked.chmod(0o700)

    def test_health_reports_path_and_writability(self, tmp_path: Path) -> None:
        """``health`` is the status-API view: string path plus live writability."""
        trace = VibeTraceLog(tmp_path / "vibe-trace.log")
        assert trace.health() == {
            "path": str(tmp_path / "vibe-trace.log"),
            "writable": True,
        }

    def test_record_swallows_io_failure(self, tmp_path: Path) -> None:
        # A directory where the file should be makes os.open raise; the sink must
        # log-and-swallow so a trace never crashes the path it observes.
        clash = tmp_path / "vibe-trace.log"
        clash.mkdir()
        VibeTraceLog(clash).record("music off")  # no exception

    def test_concurrent_appends_never_tear(self, tmp_path: Path) -> None:
        """Many processes appending concurrently produce only whole, ordered lines.

        The O_APPEND single-write contract means no line from one process ever
        interleaves into another's. A torn write would leave a line that fails
        the fixed-shape pattern; a lost write would drop the total count.
        """
        path = tmp_path / "vibe-trace.log"
        procs, per_proc = 6, 200
        ctx = multiprocessing.get_context("spawn")
        workers = [
            ctx.Process(target=_append_many, args=(str(path), pid, per_proc))
            for pid in range(procs)
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=60)
            assert worker.exitcode == 0

        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == procs * per_proc
        assert all(_LINE.match(line) for line in lines)
