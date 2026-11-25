"""Tests for filesystem operations on a contained recording target."""

from __future__ import annotations

import errno
from pathlib import Path

import pytest

from punt_vox.voxd.record_target import RecordTarget


@pytest.fixture
def target(tmp_path: Path) -> RecordTarget:
    """An isolated recording destination."""
    return RecordTarget(tmp_path / "out.mp3")


class TestRecordTarget:
    """The audio write preserves atomic placement and reports its own bytes."""

    def test_move_reports_this_calls_bytes_not_a_racing_write(
        self, target: RecordTarget, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The reported size is the bytes this call wrote, not a racing dest write."""
        src = tmp_path / "mine.mp3"
        src.write_bytes(b"mine!")  # 5 bytes -- what THIS call lands

        real_replace = Path.replace

        def racing_replace(self: Path, target: str | Path) -> Path:
            # A concurrent same-name write lands a larger file at dest right after
            # our rename; a byte count read from dest afterwards would misreport it.
            result = real_replace(self, target)
            Path(target).write_bytes(b"someone-elses-larger-bytes")
            return result

        monkeypatch.setattr(Path, "replace", racing_replace)

        write = target.place(src)

        assert write.byte_count == 5

        assert not src.exists()

    def test_fresh_uses_move_and_lands_0600(
        self, target: RecordTarget, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Fresh synthesis takes the atomic-move path (no copy) at 0600."""

        def no_copy(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("fresh synthesis must move, not copy")

        monkeypatch.setattr("punt_vox.voxd.record_target.shutil.copyfileobj", no_copy)

        src = tmp_path / "ephemeral.mp3"
        src.write_bytes(b"fresh-audio")
        src.chmod(0o644)
        write = target.place(src)

        assert write.path.read_bytes() == b"fresh-audio"
        assert write.path.stat().st_mode & 0o777 == 0o600
        assert not src.exists()

    def test_cross_device_falls_back_to_copy(
        self, target: RecordTarget, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When the atomic move can't cross filesystems, copy still lands 0600."""
        src = tmp_path / "ephemeral.mp3"
        src.write_bytes(b"xdev-audio")
        original_replace = Path.replace

        def selective_replace(self: Path, target: Path) -> Path:
            if self == src:
                raise OSError(errno.EXDEV, "cross-device link")
            return original_replace(self, target)

        monkeypatch.setattr(Path, "replace", selective_replace)

        write = target.place(src)

        assert write.path.read_bytes() == b"xdev-audio"
        assert write.path.stat().st_mode & 0o777 == 0o600
        assert not src.exists()

    def test_non_exdev_move_error_propagates(
        self, target: RecordTarget, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A non-EXDEV OSError from the move re-raises; no silent copy fallback."""
        src = tmp_path / "ephemeral.mp3"
        src.write_bytes(b"nope")

        def denied_replace(self: Path, target: Path) -> Path:
            raise OSError(errno.EACCES, "permission denied")

        monkeypatch.setattr(Path, "replace", denied_replace)

        with pytest.raises(OSError, match="permission denied"):
            target.place(src)

        assert not target.path.exists()
        assert not list(target.path.parent.glob("*.mp3.tmp"))

    def test_missing_source_leaves_no_partial_file(
        self, target: RecordTarget, tmp_path: Path
    ) -> None:
        with pytest.raises(OSError, match="No such file"):
            target.place(tmp_path / "missing.mp3")

        assert not target.path.exists()
        assert not list(target.path.parent.glob("*.mp3.tmp"))
