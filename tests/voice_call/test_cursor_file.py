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
