"""Tests for the multi-writer-safe append sink (src/punt_vox/append_log.py)."""

from __future__ import annotations

import multiprocessing
import os
import re
from collections.abc import Buffer
from pathlib import Path
from typing import TYPE_CHECKING

from punt_vox.append_log import AtomicAppendLog

if TYPE_CHECKING:
    import pytest

_LINE = re.compile(r"^proc=\d+ seq=\d+ [x]{40}$")


def _append_many(path_str: str, proc_id: int, count: int) -> None:
    """Append *count* fixed-shape lines -- a spawn-safe multiprocess worker.

    Each line is padded to a stable width so a torn or interleaved write shows up
    as a line that fails :data:`_LINE`.
    """
    sink = AtomicAppendLog(Path(path_str))
    for seq in range(count):
        sink.append(f"proc={proc_id} seq={seq} {'x' * 40}")


class TestAtomicAppendLog:
    """The sink appends whole, private, greppable lines to one durable file."""

    def test_append_writes_one_line(self, tmp_path: Path) -> None:
        sink = AtomicAppendLog(tmp_path / "x.log")
        sink.append("first")
        sink.append("second")
        assert sink.path.read_text(encoding="utf-8").splitlines() == ["first", "second"]

    def test_append_file_is_private(self, tmp_path: Path) -> None:
        sink = AtomicAppendLog(tmp_path / "x.log")
        sink.append("line")
        assert (sink.path.stat().st_mode & 0o077) == 0

    def test_append_creates_private_parent(self, tmp_path: Path) -> None:
        logs = tmp_path / "logs"
        AtomicAppendLog(logs / "x.log").append("line")
        assert (logs.stat().st_mode & 0o077) == 0

    def test_append_sanitizes_control_chars_to_one_line(self, tmp_path: Path) -> None:
        """An embedded newline/control byte stays one physical, escaped line."""
        sink = AtomicAppendLog(tmp_path / "x.log")
        sink.append("music on\nforged\rX\x07")
        raw = sink.path.read_bytes()
        assert raw.count(b"\n") == 1  # only the terminator -- no forged line
        assert not any(b < 0x20 or b == 0x7F for b in raw[:-1])
        assert sink.path.read_text(encoding="utf-8") == "music on\\nforged\\rX\\x07\n"

    def test_append_swallows_io_failure(self, tmp_path: Path) -> None:
        """A directory where the file should be makes open raise; never propagates."""
        clash = tmp_path / "x.log"
        clash.mkdir()
        AtomicAppendLog(clash).append("line")  # no exception

    def test_short_write_is_not_looped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A short ``os.write`` is surfaced to stderr, not re-issued (tear risk)."""
        real_write = os.write
        calls = {"n": 0}

        def always_short(fd: int, data: Buffer) -> int:
            calls["n"] += 1
            return real_write(fd, bytes(data)[:3])  # every write is short

        monkeypatch.setattr("punt_vox.append_log.os.write", always_short)
        AtomicAppendLog(tmp_path / "x.log").append("music off")
        assert calls["n"] == 1  # one atomic append -- no remainder loop

    def test_rotation_shifts_backups_on_oversize(self, tmp_path: Path) -> None:
        """Crossing max_bytes renames the active file to ``.1`` before the append."""
        sink = AtomicAppendLog(tmp_path / "x.log", max_bytes=64, backup_count=2)
        sink.append("a" * 50)  # under cap
        sink.append("b" * 50)  # would cross -> rotate first
        assert (tmp_path / "x.log.1").read_text(encoding="utf-8") == "a" * 50 + "\n"
        assert (tmp_path / "x.log").read_text(encoding="utf-8") == "b" * 50 + "\n"

    def test_is_writable_true_for_writable_dir(self, tmp_path: Path) -> None:
        assert AtomicAppendLog(tmp_path / "x.log").is_writable() is True

    def test_is_writable_false_when_dir_unwritable(self, tmp_path: Path) -> None:
        locked = tmp_path / "locked"
        locked.mkdir()
        locked.chmod(0o500)
        try:
            assert AtomicAppendLog(locked / "x.log").is_writable() is False
        finally:
            locked.chmod(0o700)

    def test_concurrent_appends_never_interleave(self, tmp_path: Path) -> None:
        """N processes each append M lines to one file -> N*M whole, intact lines."""
        path = tmp_path / "x.log"
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
