"""Tests for the multi-writer-safe append sink (src/punt_vox/append_log.py)."""

from __future__ import annotations

import fcntl
import multiprocessing
import os
import re
import threading
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


def _append_many_rotating(path_str: str, proc_id: int, count: int) -> None:
    """Spawn-safe worker: append to a small-cap sink so rotations fire concurrently."""
    sink = AtomicAppendLog(Path(path_str), max_bytes=4_000, backup_count=20)
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

    def test_rotate_lock_created_private(self, tmp_path: Path) -> None:
        """The stable rotate lock is created 0600, like the log it guards."""
        sink = AtomicAppendLog(tmp_path / "x.log")
        sink.append("line")
        lock = tmp_path / "x.log.rotate.lock"
        assert lock.exists()
        assert (lock.stat().st_mode & 0o077) == 0

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

    def test_is_writable_false_when_lock_uncreatable(self, tmp_path: Path) -> None:
        """A writable log is still unhealthy if the rotate lock cannot be created."""
        locked = tmp_path / "locked"
        locked.mkdir()
        sink = AtomicAppendLog(locked / "x.log")
        sink.append("seed")  # create the log (and lock) while the dir is writable
        (locked / "x.log.rotate.lock").unlink()  # remove only the lock
        locked.chmod(0o500)  # dir now read-only: lock cannot be recreated
        try:
            assert (locked / "x.log").exists()  # the log itself is present
            assert sink.is_writable() is False  # but the lock is not creatable
        finally:
            locked.chmod(0o700)

    def test_lone_surrogate_never_raises(self, tmp_path: Path) -> None:
        """A lone surrogate in the text is backslash-escaped, never a UnicodeError."""
        sink = AtomicAppendLog(tmp_path / "x.log")
        sink.append("before \ud800 after")  # no exception
        text = sink.path.read_text(encoding="utf-8")
        assert "before" in text
        assert "after" in text
        assert "\\ud800" in text  # the surrogate is escaped, one line

    def test_rotation_failure_notes_stderr(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A rename failure during rotation is surfaced to stderr, not swallowed."""
        notes: list[str] = []
        monkeypatch.setattr(AtomicAppendLog, "_to_stderr", staticmethod(notes.append))

        def _deny_replace(_self: Path, _target: Path) -> None:
            raise PermissionError("cannot rename")

        sink = AtomicAppendLog(tmp_path / "x.log", max_bytes=64, backup_count=2)
        sink.append("a" * 50)
        monkeypatch.setattr(Path, "replace", _deny_replace)
        sink.append("b" * 50)  # would rotate -> rename denied -> stderr note
        assert any("rotation stalled" in note for note in notes)

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

    def test_rotation_under_two_concurrent_writers_loses_no_lines(
        self, tmp_path: Path
    ) -> None:
        """N processes x M numbered lines across forced rotations -> N*M intact lines.

        The union of the active file and every backup contains every line exactly
        once, each intact -- rotation never tears, drops, or duplicates a line.
        Capacity ((backup_count + 1) * max_bytes) exceeds the total bytes, so no
        line ages out of the backup chain.
        """
        path = tmp_path / "x.log"
        procs, per_proc = 4, 150
        ctx = multiprocessing.get_context("spawn")
        workers = [
            ctx.Process(target=_append_many_rotating, args=(str(path), pid, per_proc))
            for pid in range(procs)
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=60)
            assert worker.exitcode == 0

        union: list[str] = []
        for backup in sorted(tmp_path.glob("x.log*")):
            if backup.name.endswith(".rotate.lock"):
                continue
            union.extend(backup.read_text(encoding="utf-8").splitlines())
        assert len(union) == procs * per_proc  # nothing lost or duplicated
        assert all(_LINE.match(line) for line in union)  # nothing torn
        assert len(set(union)) == procs * per_proc  # every (proc, seq) present once

    def test_rotation_excludes_a_concurrent_appender(self, tmp_path: Path) -> None:
        """LOCK_EX (a rotator) blocks an appender's LOCK_SH -- no renamed-file write.

        Holding the exclusive rotate lock stands in for a rotation in progress; an
        ``append`` cannot take its shared lock (and so cannot open+write) until the
        exclusive holder releases, which is what keeps a writer off a renamed inode.
        """
        sink = AtomicAppendLog(tmp_path / "x.log")
        holder = os.open(tmp_path / "x.log.rotate.lock", os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(holder, fcntl.LOCK_EX)
        done = threading.Event()

        def _writer() -> None:
            sink.append("blocked-until-release")
            done.set()

        thread = threading.Thread(target=_writer)
        thread.start()
        try:
            assert not done.wait(timeout=0.5)  # append blocks on LOCK_SH
        finally:
            fcntl.flock(holder, fcntl.LOCK_UN)
            os.close(holder)
        assert done.wait(timeout=5)  # append completes once the rotator releases
        thread.join(timeout=5)
        assert sink.path.read_text(encoding="utf-8").splitlines() == [
            "blocked-until-release"
        ]

    def test_backups_stay_private_after_rotation(self, tmp_path: Path) -> None:
        """Every rotated backup keeps 0600 -- the rename preserves the private mode."""
        sink = AtomicAppendLog(tmp_path / "x.log", max_bytes=64, backup_count=2)
        sink.append("a" * 50)
        sink.append("b" * 50)  # rotate a -> .1
        sink.append("c" * 50)  # rotate .1 -> .2, active -> .1
        for name in ("x.log", "x.log.1", "x.log.2"):
            path = tmp_path / name
            assert path.exists()
            assert (path.stat().st_mode & 0o077) == 0
