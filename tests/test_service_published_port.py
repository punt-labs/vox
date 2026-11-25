"""Tests for punt_vox.service.published_port -- what voxd published, and whose."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from punt_vox.service.backend import PortProbeError
from punt_vox.service.not_asked import NotAskedError
from punt_vox.service.process import DEFAULT_PORT, ProcessManager
from punt_vox.service.published_port import PublishedPort


def _publish(run_dir: Path, port: str = "8421", pid: str | None = None) -> Path:
    """Write serve.port (and serve.pid, this process by default); return serve.port."""
    (run_dir / "serve.port").write_text(port)
    (run_dir / "serve.pid").write_text(str(os.getpid()) if pid is None else pid)
    return run_dir / "serve.port"


def _why(path: Path, service_port: int = DEFAULT_PORT) -> str:
    """Return the gate's reason for *path*; fail if it would be asked."""
    with pytest.raises(NotAskedError) as closed:
        PublishedPort.at(path).publisher(service_port)
    return str(closed.value)


def _dead_pid() -> int:
    """Return a pid that has exited and been reaped."""
    with subprocess.Popen(["true"]) as done:
        done.wait()
        return done.pid


def test_own_files_naming_the_service_port_and_a_live_pid_open_the_gate(
    tmp_path: Path,
) -> None:
    published = PublishedPort.at(_publish(tmp_path))
    assert published.port == 8421
    assert published.pid == os.getpid()
    assert published.publisher(8421) == os.getpid()


def test_absent_port_file_publishes_nothing(tmp_path: Path) -> None:
    published = PublishedPort.at(tmp_path / "serve.port")
    assert published.port is None
    assert _why(tmp_path / "serve.port") == (
        "serve.port publishes no port; no token sent unattributed"
    )


def test_a_file_naming_another_port_keeps_the_gate_closed(tmp_path: Path) -> None:
    assert _why(_publish(tmp_path, port="9000")) == (
        "serve.port names port 9000, not the service port 8421; "
        "no token sent unattributed"
    )


def test_an_unparseable_port_file_says_so(tmp_path: Path) -> None:
    assert _why(_publish(tmp_path, port="not a port")) == (
        "serve.port does not name a port; no token sent unattributed"
    )


def test_another_users_port_file_keeps_the_gate_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The 0700 run dir makes this unreachable; the check still fails closed."""
    path = _publish(tmp_path)
    mine = os.getuid()
    monkeypatch.setattr(os, "getuid", lambda: mine + 1)
    assert _why(path) == (
        f"serve.port belongs to uid {mine}, not this user; no token sent unattributed"
    )


def test_no_pid_file_keeps_the_gate_closed(tmp_path: Path) -> None:
    (tmp_path / "serve.port").write_text("8421")
    assert _why(tmp_path / "serve.port") == (
        "serve.pid publishes no pid; no token sent unattributed"
    )


@pytest.mark.parametrize(
    "text", ["", "abc", "0", "-5", "99999999999999999999", str(2**31)]
)
def test_an_unparseable_pid_file_says_so(tmp_path: Path, text: str) -> None:
    """Digits past pid_t would make os.kill raise OverflowError: not a pid."""
    assert _why(_publish(tmp_path, pid=text)) == (
        "serve.pid does not name a pid; no token sent unattributed"
    )


def test_a_dead_publisher_keeps_the_gate_closed(tmp_path: Path) -> None:
    """The stale file after a crash: whatever binds the port next is not asked."""
    dead = _dead_pid()
    assert _why(_publish(tmp_path, pid=str(dead))) == (
        f"voxd pid {dead} that published serve.port is gone; no token sent unattributed"
    )


def test_another_users_publisher_keeps_the_gate_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _publish(tmp_path, pid="4242")

    def refused(pid: int, sig: int) -> None:
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(os, "kill", refused)
    assert _why(path) == (
        "pid 4242 named in serve.pid is another user's process; "
        "no token sent unattributed"
    )


def test_another_users_pid_file_keeps_the_gate_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _publish(tmp_path)
    mine = os.getuid()
    real_fstat = os.fstat

    def foreign_pid_file(fd: int) -> os.stat_result:
        st = real_fstat(fd)
        if st.st_size == len(str(os.getpid())):  # serve.pid, not "8421"
            return os.stat_result((*st[:4], mine + 1, *st[5:]))
        return st

    monkeypatch.setattr(os, "fstat", foreign_pid_file)
    assert _why(path) == (
        f"serve.pid belongs to uid {mine + 1}, not this user; "
        "no token sent unattributed"
    )


def test_a_file_that_cannot_be_statted_raises_with_the_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stat failure keeps the OS error text and is never read as "not ours"."""
    path = _publish(tmp_path)

    def denied(fd: int) -> os.stat_result:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(os, "fstat", denied)
    with pytest.raises(PortProbeError) as caught:
        PublishedPort.at(path)
    assert str(caught.value).startswith(f"cannot stat {path}: ")
    assert "Permission denied" in str(caught.value)
    assert "not this user" not in str(caught.value)


def test_port_owner_and_pid_come_from_the_files_that_were_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No stat of a path: a file renamed in after the read is never the owner."""
    path = _publish(tmp_path)
    real_stat = Path.stat

    def never_a_run_file(self: Path, *, follow_symlinks: bool = True) -> os.stat_result:
        if self.name in {"serve.port", "serve.pid"}:
            msg = "stat(path) would describe whatever is at the path now"
            raise AssertionError(msg)
        return real_stat(self, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "stat", never_a_run_file)
    assert PublishedPort.at(path).publisher(8421) == os.getpid()


def test_an_unreadable_file_raises(tmp_path: Path) -> None:
    (tmp_path / "serve.port").write_bytes(b"\xff\xfe")
    with pytest.raises(PortProbeError, match="invalid UTF-8"):
        PublishedPort.at(tmp_path / "serve.port")


def test_process_manager_names_its_port_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ProcessManager, "_run_dir", staticmethod(lambda: tmp_path))
    _publish(tmp_path)
    assert PublishedPort.at(ProcessManager().port_file).publisher(8421) == os.getpid()
