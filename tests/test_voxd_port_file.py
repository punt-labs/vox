"""``serve.port`` ownership: a voxd removes the file only while it is still its own.

Two daemons share one run directory and both write ``serve.port`` -- across a
restart, both on 8421, so the text alone cannot say whose file it is. A voxd
shutting down after another has written the file must leave that file alone,
or every client loses the running daemon.
"""

from __future__ import annotations

import errno
import fcntl
import logging
import os
import threading
from pathlib import Path

import pytest

from punt_vox.voxd.port_file import PortFile


def test_shutdown_removes_its_own_port_file(tmp_path: Path) -> None:
    a = PortFile(tmp_path)
    a.write(8421)
    assert (tmp_path / "serve.port").read_text() == "8421"
    a.remove()
    assert not (tmp_path / "serve.port").exists()


def test_shutdown_leaves_a_newer_daemons_port_file_on_the_same_port(
    tmp_path: Path,
) -> None:
    """A restart: A and B both serve 8421; A's late shutdown keeps B's file."""
    a, b = PortFile(tmp_path), PortFile(tmp_path)
    a.write(8421)
    b.write(8421)
    a.remove()
    assert (tmp_path / "serve.port").read_text() == "8421"
    b.remove()
    assert not (tmp_path / "serve.port").exists()


def test_shutdown_leaves_a_newer_daemons_port_file_on_another_port(
    tmp_path: Path,
) -> None:
    a, b = PortFile(tmp_path), PortFile(tmp_path)
    a.write(8421)
    b.write(9000)
    a.remove()
    assert (tmp_path / "serve.port").read_text() == "9000"


def test_shutdown_with_no_port_file_is_quiet(tmp_path: Path) -> None:
    a = PortFile(tmp_path)
    a.write(8421)
    (tmp_path / "serve.port").unlink()
    a.remove()
    assert not (tmp_path / "serve.port").exists()


def test_a_daemon_that_never_wrote_removes_nothing(tmp_path: Path) -> None:
    """One that never bound its port has no file to claim."""
    PortFile(tmp_path).write(8421)
    PortFile(tmp_path).remove()
    assert (tmp_path / "serve.port").read_text() == "8421"


def _temporaries(run_dir: Path) -> list[str]:
    """Return the half-published ``.serve.port.<pid>`` files left behind."""
    return [
        p.name
        for p in run_dir.iterdir()
        if p.name.startswith(".serve.port.") and not p.name.endswith(".lock")
    ]


def test_write_leaves_no_temporary_file(tmp_path: Path) -> None:
    PortFile(tmp_path).write(8421)
    assert (tmp_path / "serve.port").read_text() == "8421"
    assert _temporaries(tmp_path) == []


def test_write_records_the_file_it_wrote_not_a_later_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second voxd renaming its file in just after ours is never ours."""
    real_replace = Path.replace

    def replace_then_newer_daemon(self: Path, target: Path) -> Path:
        done = real_replace(self, target)
        theirs = tmp_path / ".serve.port.other"
        theirs.write_text("9000")
        real_replace(theirs, target)
        return done

    a = PortFile(tmp_path)
    monkeypatch.setattr(Path, "replace", replace_then_newer_daemon)
    a.write(8421)
    monkeypatch.undo()
    a.remove()
    assert (tmp_path / "serve.port").read_text() == "9000"


def test_a_failed_rename_leaves_no_temporary_file_and_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(self: Path, target: Path) -> Path:
        raise OSError(errno.EXDEV, "cross-device rename")

    monkeypatch.setattr(Path, "replace", refuse)
    with pytest.raises(OSError, match="cross-device"):
        PortFile(tmp_path).write(8421)
    assert _temporaries(tmp_path) == []
    assert not (tmp_path / "serve.port").exists()


def test_removal_paused_after_its_check_never_deletes_a_newer_daemons_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Check-and-unlink is one step: a publish cannot land between them."""
    a, b = PortFile(tmp_path), PortFile(tmp_path)
    a.write(8421)
    checked, resume = threading.Event(), threading.Event()
    real_unlink = Path.unlink

    def unlink_after_a_pause(self: Path, missing_ok: bool = False) -> None:
        if self.name == "serve.port" and not checked.is_set():
            checked.set()
            resume.wait(timeout=5.0)
        real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", unlink_after_a_pause)
    remover = threading.Thread(target=a.remove)
    remover.start()
    assert checked.wait(timeout=5.0)
    publisher = threading.Thread(target=b.write, args=(9000,))
    publisher.start()
    publisher.join(timeout=0.5)
    resume.set()
    remover.join(timeout=10.0)
    publisher.join(timeout=10.0)
    assert not remover.is_alive()
    assert not publisher.is_alive()
    assert (tmp_path / "serve.port").read_text() == "9000"


def test_an_unreadable_port_file_is_logged_not_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A stat that fails for any reason but "gone" names the path and the error."""
    a = PortFile(tmp_path)
    a.write(8421)
    real_stat = Path.stat

    def denied(self: Path, *, follow_symlinks: bool = True) -> os.stat_result:
        if self.name == "serve.port":
            raise PermissionError(errno.EACCES, "Permission denied", str(self))
        return real_stat(self, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "stat", denied)
    with caplog.at_level(logging.WARNING, logger="punt_vox.voxd.port_file"):
        a.remove()
    warning = next(r for r in caplog.records if r.levelno == logging.WARNING)
    assert "serve.port" in warning.getMessage()
    assert "Permission denied" in warning.getMessage()


def test_a_lock_that_cannot_be_taken_is_logged_not_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    a = PortFile(tmp_path)
    a.write(8421)

    def no_locks(fd: object, op: int) -> None:
        raise OSError(errno.ENOLCK, "No locks available")

    monkeypatch.setattr(fcntl, "flock", no_locks)
    with caplog.at_level(logging.WARNING, logger="punt_vox.voxd.port_file"):
        a.remove()
    warning = next(r for r in caplog.records if r.levelno == logging.WARNING)
    assert "serve.port" in warning.getMessage()
    assert "No locks available" in warning.getMessage()
    assert (tmp_path / "serve.port").read_text() == "8421"


def test_a_file_with_this_inode_but_new_times_is_not_this_daemons(
    tmp_path: Path,
) -> None:
    """Inode reuse: a freed inode handed to a newer voxd's file is not ours."""
    a = PortFile(tmp_path)
    a.write(8421)
    port_file = tmp_path / "serve.port"
    before = port_file.stat()
    os.utime(port_file, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000))
    a.remove()
    assert port_file.read_text() == "8421"


def test_a_symlink_at_the_temp_path_is_never_written_through(tmp_path: Path) -> None:
    victim = tmp_path / "victim"
    victim.write_text("precious")
    (tmp_path / f".serve.port.{os.getpid()}").symlink_to(victim)
    PortFile(tmp_path).write(8421)
    assert victim.read_text() == "precious"
    assert (tmp_path / "serve.port").read_text() == "8421"


def test_the_published_port_file_is_private(tmp_path: Path) -> None:
    PortFile(tmp_path).write(8421)
    assert (tmp_path / "serve.port").stat().st_mode & 0o777 == 0o600
