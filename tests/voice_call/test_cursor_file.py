from __future__ import annotations

import os
import stat
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from punt_vox.voxd.voice_call.cursor_file import CursorFile

if TYPE_CHECKING:
    from pathlib import Path


def test_cursor_file_creates_and_replaces_private_utf8_file(tmp_path: Path) -> None:
    path = tmp_path / "departure.json"
    file = CursorFile(path)

    with pytest.raises(FileNotFoundError):
        file.read()
    file.replace('{"séance": 1}\n')
    file.replace('{"séance": 2}\n')

    assert file.read() == '{"séance": 2}\n'
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_cursor_syncs_directory_after_renaming(tmp_path: Path) -> None:
    directory = tmp_path / "calls"
    directory.mkdir()
    path = directory / "departure.json"
    syncs: list[tuple[int, bool]] = []
    fsync = os.fsync

    def observe(fd: int) -> None:
        syncs.append((os.fstat(fd).st_ino, path.exists()))
        fsync(fd)

    with patch("punt_vox.voxd.voice_call.cursor_file.os.fsync", side_effect=observe):
        CursorFile(path).replace("{}")

    assert syncs == [
        (tmp_path.stat().st_ino, False),
        (path.stat().st_ino, False),
        (directory.stat().st_ino, True),
    ]


def test_first_cursor_syncs_each_new_directories_parent(tmp_path: Path) -> None:
    parent = tmp_path / "vox"
    directory = parent / "calls"
    path = directory / "departure.json"
    syncs: list[int] = []
    fsync = os.fsync

    def observe(fd: int) -> None:
        assert parent.exists()
        inode = os.fstat(fd).st_ino
        if inode == parent.stat().st_ino:
            assert directory.exists()
        syncs.append(inode)
        fsync(fd)

    with patch("punt_vox.voxd.voice_call.cursor_file.os.fsync", side_effect=observe):
        CursorFile(path).replace("{}")

    assert syncs == [
        tmp_path.stat().st_ino,
        parent.stat().st_ino,
        path.stat().st_ino,
        directory.stat().st_ino,
    ]


def test_cursor_file_does_not_read_symlink_target(tmp_path: Path) -> None:
    target = tmp_path / "other.json"
    target.write_text('{"worker": 99}')
    link = tmp_path / "departure.json"
    link.symlink_to(target)

    with pytest.raises(OSError):
        CursorFile(link).read()


def test_cursor_file_does_not_replace_symlink_target(tmp_path: Path) -> None:
    target = tmp_path / "other.json"
    target.write_text('{"worker": 99}')
    link = tmp_path / "departure.json"
    link.symlink_to(target)

    with pytest.raises(ValueError, match="not a regular file"):
        CursorFile(link).replace('{"worker": 1}')

    assert link.is_symlink()
    assert target.read_text() == '{"worker": 99}'
