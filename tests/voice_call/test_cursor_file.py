from __future__ import annotations

import stat
from typing import TYPE_CHECKING

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
