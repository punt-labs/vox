"""``StalePortFile``: serve.port read once, cleared only while it is that file."""

from __future__ import annotations

import errno
import fcntl
from typing import TYPE_CHECKING

import pytest

from punt_vox.service.backend import PortProbeError
from punt_vox.service.port_file_cleanup import PortFileCleanup
from punt_vox.service.stale_port_file import StalePortFile
from punt_vox.sibling_lock import SiblingLock

if TYPE_CHECKING:
    from pathlib import Path


def test_read_takes_the_port_from_the_file_it_pins(tmp_path: Path) -> None:
    port_file = tmp_path / "serve.port"
    port_file.write_text("9000\n")
    assert StalePortFile.read(port_file).port == 9000


def test_read_of_an_unparseable_file_has_no_port_and_still_clears_it(
    tmp_path: Path,
) -> None:
    """A crash leftover is stale whoever wrote it."""
    port_file = tmp_path / "serve.port"
    port_file.write_text("84x")
    stale = StalePortFile.read(port_file)
    assert stale.port is None
    assert stale.discard(8421) is PortFileCleanup.REMOVED
    assert not port_file.exists()


def test_a_file_naming_another_port_is_left(tmp_path: Path) -> None:
    port_file = tmp_path / "serve.port"
    port_file.write_text("9000")
    assert StalePortFile.read(port_file).discard(8421) is PortFileCleanup.OTHER_PORT
    assert port_file.read_text() == "9000"


def test_discard_takes_the_lock_voxd_publishes_with(tmp_path: Path) -> None:
    """The CLI and voxd's PortFile serialize on one sibling lock file."""
    port_file = tmp_path / "serve.port"
    port_file.write_text("8421")
    assert StalePortFile.read(port_file).discard(8421) is PortFileCleanup.REMOVED
    assert SiblingLock(port_file).path.exists()


def test_a_lock_that_cannot_be_taken_raises_a_probe_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    port_file = tmp_path / "serve.port"
    port_file.write_text("8421")

    def no_locks(fd: object, op: int) -> None:
        raise OSError(errno.ENOLCK, "No locks available")

    monkeypatch.setattr(fcntl, "flock", no_locks)
    with pytest.raises(PortProbeError, match=r"serve\.port.*No locks available"):
        StalePortFile.read(port_file)
    assert port_file.read_text() == "8421"


def test_a_file_published_after_the_read_is_left(tmp_path: Path) -> None:
    """Same port, new file: a voxd published it after the cleanup read."""
    port_file = tmp_path / "serve.port"
    port_file.write_text("8421")
    stale = StalePortFile.read(port_file)
    newer = tmp_path / ".serve.port.newer"
    newer.write_text("8421")
    newer.replace(port_file)
    assert stale.discard(8421) is PortFileCleanup.REPLACED
    assert port_file.read_text() == "8421"


def test_a_file_published_where_none_was_read_is_left(tmp_path: Path) -> None:
    port_file = tmp_path / "serve.port"
    stale = StalePortFile.read(port_file)
    port_file.write_text("8421")
    assert stale.discard(8421) is PortFileCleanup.REPLACED
    assert port_file.exists()


def test_cleanup_with_no_run_dir_is_absent_and_creates_nothing(tmp_path: Path) -> None:
    """stop on a host with no run dir writes nothing: no dir, no lock file."""
    run_dir = tmp_path / "run"
    stale = StalePortFile.read(run_dir / "serve.port")
    assert stale.discard(8421) is PortFileCleanup.ABSENT
    assert not run_dir.exists()


def test_cleanup_with_no_port_file_takes_no_lock(tmp_path: Path) -> None:
    port_file = tmp_path / "serve.port"
    assert StalePortFile.read(port_file).discard(8421) is PortFileCleanup.ABSENT
    assert list(tmp_path.iterdir()) == []
