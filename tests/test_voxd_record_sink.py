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

    def test_fresh_uses_move_and_lands_0600(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Fresh synthesis takes the atomic-move path (no copy) at 0600."""

        # The copy path calls shutil.copyfileobj -- make it fail loudly so a
        # regression from the move fast-path to the copy path is caught.
        def no_copy(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("fresh synthesis must move, not copy")

        monkeypatch.setattr("punt_vox.voxd.record_sink.shutil.copyfileobj", no_copy)

        src = tmp_path / "ephemeral.mp3"
        src.write_bytes(b"fresh-audio")
        src.chmod(0o644)  # ephemeral source is not private until we chmod dest
        dest = tmp_path / "out.mp3"
        sink = RecordSink(tmp_path, dest)

        write = sink.place(source=src, text="hi", cached=False)

        assert write.path == dest
        assert dest.read_bytes() == b"fresh-audio"
        assert write.byte_count == len(b"fresh-audio")
        assert dest.stat().st_mode & 0o777 == 0o600
        assert not src.exists()

    def test_cross_device_falls_back_to_copy(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When the atomic move can't cross filesystems, copy still lands 0600."""
        src = tmp_path / "ephemeral.mp3"
        src.write_bytes(b"xdev-audio")
        dest = tmp_path / "out.mp3"
        sink = RecordSink(tmp_path, dest)

        original_replace = Path.replace

        def selective_replace(self: Path, target: Path) -> Path:
            if self == src:  # the fast-path move -> simulate EXDEV
                raise OSError("cross-device link")
            return original_replace(self, target)  # the copy path's temp rename

        monkeypatch.setattr(Path, "replace", selective_replace)

        write = sink.place(source=src, text="hi", cached=False)

        assert write.path == dest
        assert dest.read_bytes() == b"xdev-audio"
        assert write.byte_count == len(b"xdev-audio")
        assert dest.stat().st_mode & 0o777 == 0o600
        assert not src.exists()

    def test_cached_copy_lands_0600_and_preserves_source(self, tmp_path: Path) -> None:
        """A cache hit is copied (source preserved) and the dest is 0600."""
        src = tmp_path / "cache_entry.mp3"
        src.write_bytes(b"cached-audio")
        dest = tmp_path / "out.mp3"
        sink = RecordSink(tmp_path, dest)

        write = sink.place(source=src, text="hi", cached=True)

        assert src.exists()  # cache entry preserved
        assert dest.read_bytes() == b"cached-audio"
        assert write.byte_count == len(b"cached-audio")
        assert dest.stat().st_mode & 0o777 == 0o600

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
        """A cleanup failure after the commit point must not fail a done write.

        The best-effort source unlink only runs on the copy path, so force the
        move to fall back to copy (EXDEV) and then fail the source unlink.
        """
        src = tmp_path / "ephemeral.mp3"
        src.write_bytes(b"abcd")
        dest = tmp_path / "out.mp3"
        sink = RecordSink(tmp_path, dest)

        original_replace = Path.replace

        def force_copy(self: Path, target: Path) -> Path:
            if self == src:  # fail the fast-path move -> fall back to copy
                raise OSError("cross-device link")
            return original_replace(self, target)

        monkeypatch.setattr(Path, "replace", force_copy)

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
