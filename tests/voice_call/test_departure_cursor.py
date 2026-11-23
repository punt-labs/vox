from __future__ import annotations

import os
import stat
from pathlib import Path
from unittest.mock import patch

import pytest

from punt_vox.voxd.voice_call.departure_cursor import DepartureCursor


def test_non_regular_cursor_is_not_opened_or_replaced(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "departure.json"
    os.mkfifo(path)
    cursor = DepartureCursor(path)

    with patch.object(Path, "read_text", side_effect=AssertionError("opened FIFO")):
        assert not cursor.load([("worker", 3)])

    assert cursor.sequence("worker") == 3
    assert "Ignoring invalid call departure cursor" in caplog.text
    with pytest.raises(ValueError, match="not a regular file"):
        cursor.save({})
    assert stat.S_ISFIFO(path.stat().st_mode)
