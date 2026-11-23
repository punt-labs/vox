from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

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


def test_fifo_cursor_is_not_read_or_replaced(tmp_path: Path) -> None:
    path = tmp_path / "departure.json"
    os.mkfifo(path)
    data = b'{"worker": 99}'
    reader = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    try:
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_NONBLOCK), "wb") as writer:
            writer.write(data)
        probe = subprocess.run(
            [
                sys.executable,
                "-c",
                """
from pathlib import Path
import sys
from punt_vox.voxd.voice_call.departure_cursor import DepartureCursor

cursor = DepartureCursor(Path(sys.argv[1]))
assert not cursor.load([("worker", 3)])
assert cursor.sequence("worker") == 3
""",
                str(path),
            ],
            capture_output=True,
            check=False,
            text=True,
            timeout=5,
        )
        assert probe.returncode == 0, probe.stderr
        assert "Ignoring invalid call departure cursor" in probe.stderr
        assert os.read(reader, len(data)) == data
    finally:
        os.close(reader)

    with pytest.raises(ValueError, match="not a regular file"):
        DepartureCursor(path).save({})
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
