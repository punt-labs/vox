"""Tests for punt_vox.voxd.record_sink -- atomic daemon-side record placement."""

from __future__ import annotations

from pathlib import Path

import pytest

from punt_vox.types import generate_filename
from punt_vox.voxd.record_sink import RecordSink


class TestRecordSink:
    """place() lands audio at the destination atomically and reports its size."""

    def test_explicit_path_lands_bytes(self, tmp_path: Path) -> None:
        src = tmp_path / "src.mp3"
        src.write_bytes(b"\xff\xfb" * 100)
        dest = tmp_path / "sub" / "out.mp3"
        sink = RecordSink(tmp_path, dest)

        write = sink.place(source=src, text="hello", cached=False)

        assert write.path == dest
        assert dest.read_bytes() == b"\xff\xfb" * 100
        assert write.byte_count == 200

    def test_dir_names_by_content_hash(self, tmp_path: Path) -> None:
        src = tmp_path / "src.mp3"
        src.write_bytes(b"\x00\x01\x02")
        out_dir = tmp_path / "out"
        sink = RecordSink(out_dir)

        write = sink.place(source=src, text="some text", cached=False)

        assert write.path == out_dir / generate_filename("some text")
        assert write.path.exists()

    def test_cached_source_is_preserved(self, tmp_path: Path) -> None:
        """A cache-hit source must survive placement -- never move the cache."""
        src = tmp_path / "cache_entry.mp3"
        src.write_bytes(b"cached")
        sink = RecordSink(tmp_path, tmp_path / "out.mp3")

        sink.place(source=src, text="hi", cached=True)

        assert src.exists()

    def test_fresh_source_is_removed(self, tmp_path: Path) -> None:
        """An ephemeral fresh-synthesis source is cleaned up after landing."""
        src = tmp_path / "ephemeral.mp3"
        src.write_bytes(b"fresh")
        sink = RecordSink(tmp_path, tmp_path / "out.mp3")

        sink.place(source=src, text="hi", cached=False)

        assert not src.exists()

    def test_missing_source_leaves_no_partial_file(self, tmp_path: Path) -> None:
        """A copy failure raises and leaves no partial file at the destination."""
        dest = tmp_path / "out.mp3"
        sink = RecordSink(tmp_path, dest)

        with pytest.raises(OSError, match="No such file"):
            sink.place(source=tmp_path / "missing.mp3", text="hi", cached=False)

        assert not dest.exists()
        # The sibling temp is cleaned up too -- no orphan *.mp3.tmp left behind.
        assert not list(tmp_path.glob("*.mp3.tmp"))

    def test_post_commit_unlink_failure_does_not_fail_write(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A cleanup failure after the commit point must not fail a done write."""
        src = tmp_path / "ephemeral.mp3"
        src.write_bytes(b"abcd")
        dest = tmp_path / "out.mp3"
        sink = RecordSink(tmp_path, dest)

        original_unlink = Path.unlink

        def failing_unlink(self: Path, *args: object, **kwargs: object) -> None:
            if self == src:
                raise OSError("cannot unlink source")
            original_unlink(self, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(Path, "unlink", failing_unlink)

        write = sink.place(source=src, text="hi", cached=False)

        assert write.path == dest
        assert dest.read_bytes() == b"abcd"
        assert write.byte_count == 4  # taken from the temp before the commit
