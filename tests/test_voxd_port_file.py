"""``serve.port`` ownership: a voxd removes the file only while it is still its own.

Two daemons share one run directory and both write ``serve.port`` -- across a
restart, both on 8421, so the text alone cannot say whose file it is. A voxd
shutting down after another has written the file must leave that file alone,
or every client loses the running daemon.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from punt_vox.voxd.port_file import PortFile

if TYPE_CHECKING:
    from pathlib import Path


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


def test_write_leaves_no_temporary_file(tmp_path: Path) -> None:
    PortFile(tmp_path).write(8421)
    assert [p.name for p in tmp_path.iterdir()] == ["serve.port"]
