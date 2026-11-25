"""Tests for punt_vox.service.published_port -- the port voxd published, and whose."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from punt_vox.service.backend import PortProbeError
from punt_vox.service.not_asked import NotAskedError
from punt_vox.service.process import DEFAULT_PORT, ProcessManager
from punt_vox.service.published_port import PublishedPort


def _why(path: Path, service_port: int = DEFAULT_PORT) -> str:
    """Return the gate's reason for *path*; fail if it would be asked."""
    unasked = PublishedPort.at(path).unasked(service_port)
    assert isinstance(unasked, NotAskedError)
    return str(unasked)


def test_absent_file_publishes_nothing(tmp_path: Path) -> None:
    published = PublishedPort.at(tmp_path / "serve.port")
    assert published.port is None
    assert _why(tmp_path / "serve.port") == (
        "serve.port publishes no port; no token sent unattributed"
    )


def test_own_file_naming_the_service_port_opens_the_gate(tmp_path: Path) -> None:
    (tmp_path / "serve.port").write_text("8421\n")
    published = PublishedPort.at(tmp_path / "serve.port")
    assert published.port == 8421
    assert published.unasked(8421) is None


def test_a_file_naming_another_port_keeps_the_gate_closed(tmp_path: Path) -> None:
    (tmp_path / "serve.port").write_text("9000")
    assert _why(tmp_path / "serve.port") == (
        "serve.port names port 9000, not the service port 8421; "
        "no token sent unattributed"
    )


def test_an_unparseable_file_says_so(tmp_path: Path) -> None:
    (tmp_path / "serve.port").write_text("not a port")
    assert PublishedPort.at(tmp_path / "serve.port").port is None
    assert _why(tmp_path / "serve.port") == (
        "serve.port does not name a port; no token sent unattributed"
    )


def test_another_users_file_keeps_the_gate_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The 0700 run dir makes this unreachable; the check still fails closed."""
    (tmp_path / "serve.port").write_text("8421")
    other = os.getuid() + 1
    monkeypatch.setattr(os, "getuid", lambda: other)
    assert _why(tmp_path / "serve.port") == (
        f"serve.port belongs to uid {os.getuid() - 1}, not this user; "
        "no token sent unattributed"
    )


def test_a_file_that_cannot_be_statted_raises_with_the_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stat failure keeps the OS error text and is never read as "not ours"."""
    (tmp_path / "serve.port").write_text("8421")

    def denied(fd: int) -> os.stat_result:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(os, "fstat", denied)
    with pytest.raises(PortProbeError) as caught:
        PublishedPort.at(tmp_path / "serve.port")
    assert str(caught.value).startswith(f"cannot stat {tmp_path / 'serve.port'}: ")
    assert "Permission denied" in str(caught.value)
    assert "not this user" not in str(caught.value)


def test_port_and_owner_come_from_the_file_that_was_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No stat of the path: a file renamed in after the read is never the owner."""
    (tmp_path / "serve.port").write_text("8421")
    real_stat = Path.stat

    def never_the_port_file(
        self: Path, *, follow_symlinks: bool = True
    ) -> os.stat_result:
        if self.name == "serve.port":
            msg = "stat(path) would describe whatever is at the path now"
            raise AssertionError(msg)
        return real_stat(self, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "stat", never_the_port_file)
    assert PublishedPort.at(tmp_path / "serve.port").unasked(8421) is None


def test_an_unreadable_file_raises(tmp_path: Path) -> None:
    (tmp_path / "serve.port").write_bytes(b"\xff\xfe")
    with pytest.raises(PortProbeError, match="invalid UTF-8"):
        PublishedPort.at(tmp_path / "serve.port")


def test_process_manager_names_its_port_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ProcessManager, "_run_dir", staticmethod(lambda: tmp_path))
    (tmp_path / "serve.port").write_text("8421")
    assert PublishedPort.at(ProcessManager().port_file).unasked(8421) is None
