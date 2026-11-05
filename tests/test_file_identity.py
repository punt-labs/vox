"""``FileIdentity``: one file, told apart from any later file on its inode."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from punt_vox.file_identity import FileIdentity

if TYPE_CHECKING:
    from pathlib import Path


def test_the_same_untouched_file_has_one_identity(tmp_path: Path) -> None:
    path = tmp_path / "f"
    path.write_text("x")
    assert FileIdentity.at(path) == FileIdentity.at(path)


def test_same_device_and_inode_with_a_different_ctime_is_another_file() -> None:
    first = FileIdentity(device=1, inode=2, mtime_ns=3, ctime_ns=4)
    reused = FileIdentity(device=1, inode=2, mtime_ns=3, ctime_ns=5)
    assert first != reused


def test_a_rewrite_on_the_same_inode_is_another_file(tmp_path: Path) -> None:
    path = tmp_path / "f"
    path.write_text("x")
    before = FileIdentity.at(path)
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
    assert FileIdentity.at(path) != before


def test_no_file_has_no_identity(tmp_path: Path) -> None:
    assert FileIdentity.at(tmp_path / "missing") is None


def test_of_an_open_descriptor_matches_the_path(tmp_path: Path) -> None:
    path = tmp_path / "f"
    path.write_text("x")
    with path.open("rb") as handle:
        assert FileIdentity.of(os.fstat(handle.fileno())) == FileIdentity.at(path)
