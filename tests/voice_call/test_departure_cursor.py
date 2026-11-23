from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from unittest.mock import patch

import pytest

from punt_vox.voxd.voice_call.departure_cursor import DepartureCursor


def test_missing_cursor_uses_baseline_without_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "departure.json"
    cursor = DepartureCursor(path)

    assert not cursor.load([("worker", 3)])

    assert cursor.sequence("worker") == 3
    assert cursor.sequence("missing") == 0
    assert not path.exists()
    assert "Ignoring invalid call departure cursor" not in caplog.text


def test_corrupt_cursor_is_rewritten_on_save(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "departure.json"
    path.write_text('{"worker": -1}', encoding="utf-8")
    cursor = DepartureCursor(path)

    assert not cursor.load([("worker", 3)])
    assert "Ignoring invalid call departure cursor" in caplog.text
    assert json.loads(path.read_text()) == {"worker": -1}

    cursor.save({})

    assert json.loads(path.read_text()) == {"worker": 3}


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


def test_cursor_roundtrip_keeps_highest_known_sequences(tmp_path: Path) -> None:
    path = tmp_path / "departure.json"
    cursor = DepartureCursor(path)
    assert not cursor.load([("worker", 3)])
    cursor.record("worker", 5)
    cursor.record("worker", 4)
    cursor.save({"worker": 2, "other": 7})

    restored = DepartureCursor(path)
    assert restored.load([("worker", 99), ("unused", 3)])
    assert restored.sequence("worker") == 5
    assert restored.sequence("other") == 7
    assert restored.sequence("unused") == 0
