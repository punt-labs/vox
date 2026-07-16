"""Tests for the record-tool file sink (src/punt_vox/recording.py)."""

from __future__ import annotations

from pathlib import Path

from punt_vox.recording import RecordingSink


class TestRecordingSink:
    """The sink writes audio and describes the result record."""

    def test_hashed_write_returns_entry(self, tmp_path: Path) -> None:
        sink = RecordingSink(tmp_path)
        entry = sink.entry("hello", "roger", "elevenlabs", b"\xff\xfb\x00")
        path = Path(str(entry["path"]))
        assert path.parent == tmp_path
        assert path.suffix == ".mp3"
        assert path.read_bytes() == b"\xff\xfb\x00"
        assert entry["text"] == "hello"
        assert entry["voice"] == "roger"
        assert entry["provider"] == "elevenlabs"
        assert entry["bytes"] == 3

    def test_same_text_hashes_to_same_path(self, tmp_path: Path) -> None:
        sink = RecordingSink(tmp_path)
        first = sink.entry("same", None, None, b"a")
        second = sink.entry("same", None, None, b"aa")
        assert first["path"] == second["path"]

    def test_explicit_path_pins_output(self, tmp_path: Path) -> None:
        target = tmp_path / "custom.mp3"
        sink = RecordingSink(tmp_path, target)
        entry = sink.entry("anything", None, None, b"z")
        assert entry["path"] == str(target)
        assert target.read_bytes() == b"z"

    def test_write_creates_missing_parents(self, tmp_path: Path) -> None:
        target = tmp_path / "deep" / "nested" / "out.mp3"
        sink = RecordingSink(tmp_path, target)
        written = sink.write("t", b"q")
        assert written == target
        assert target.read_bytes() == b"q"
