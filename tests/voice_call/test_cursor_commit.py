from __future__ import annotations

import stat
from typing import TYPE_CHECKING

import pytest

from punt_vox.voxd.voice_call.cursor_commit import CursorCommit

if TYPE_CHECKING:
    from pathlib import Path


def test_prepare_keeps_existing_permissions_and_rejects_a_file(tmp_path: Path) -> None:
    directory = tmp_path / "calls"
    directory.mkdir(mode=0o755)
    original_mode = stat.S_IMODE(directory.stat().st_mode)
    CursorCommit.prepare(directory)
    assert stat.S_IMODE(directory.stat().st_mode) == original_mode

    path = tmp_path / "not-a-directory"
    path.touch()
    with pytest.raises(FileExistsError):
        CursorCommit.prepare(path)
