"""Tests for punt_vox.service.port_file_cleanup -- clearing a stale serve.port."""

from __future__ import annotations

import errno
import fcntl
from typing import TYPE_CHECKING

import pytest

from punt_vox.service.backend import PortProbeError
from punt_vox.service.port_file_cleanup import PortFileCleanup, StalePortFile
from punt_vox.sibling_lock import SiblingLock

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    ("result", "settled"),
    [
        (PortFileCleanup.REMOVED, True),
        (PortFileCleanup.ABSENT, True),
        (PortFileCleanup.OTHER_PORT, True),
        (PortFileCleanup.HELD, False),
        (PortFileCleanup.REPLACED, True),
    ],
)
def test_only_a_decline_leaves_the_cleanup_open(
    result: PortFileCleanup, settled: bool
) -> None:
    assert result.settled is settled


def test_discard_takes_the_lock_voxd_publishes_with(tmp_path: Path) -> None:
    """The CLI and voxd's PortFile serialize on one sibling lock file."""
    port_file = tmp_path / "serve.port"
    port_file.write_text("8421")
    assert StalePortFile.as_found(port_file).discard(8421) is PortFileCleanup.REMOVED
    assert SiblingLock(port_file).path.exists()


def test_a_lock_that_cannot_be_taken_raises_a_probe_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    port_file = tmp_path / "serve.port"
    port_file.write_text("8421")

    def no_locks(fd: object, op: int) -> None:
        raise OSError(errno.ENOLCK, "No locks available")

    monkeypatch.setattr(fcntl, "flock", no_locks)
    with pytest.raises(PortProbeError, match=r"cannot lock .*serve\.port"):
        StalePortFile.as_found(port_file).discard(8421)
    assert port_file.read_text() == "8421"


def test_a_file_published_after_the_cleanup_looked_is_left(tmp_path: Path) -> None:
    """Same port, new file: a voxd published it after the cleanup decided."""
    port_file = tmp_path / "serve.port"
    port_file.write_text("8421")
    stale = StalePortFile.as_found(port_file)
    newer = tmp_path / ".serve.port.newer"
    newer.write_text("8421")
    newer.replace(port_file)
    assert stale.discard(8421) is PortFileCleanup.REPLACED
    assert port_file.read_text() == "8421"


def test_a_file_published_where_none_was_found_is_left(tmp_path: Path) -> None:
    port_file = tmp_path / "serve.port"
    stale = StalePortFile.as_found(port_file)
    port_file.write_text("8421")
    assert stale.discard(8421) is PortFileCleanup.REPLACED
    assert port_file.exists()
