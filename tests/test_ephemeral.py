"""Tests for punt_vox.ephemeral."""

from __future__ import annotations

import os
from pathlib import Path

from punt_vox.ephemeral import (
    clean_ephemeral,
    ephemeral_output_dir,
    remove_ephemeral_dir,
)


class TestEphemeralOutputDir:
    def test_creates_directory(self, tmp_path: Path) -> None:
        os.chdir(tmp_path)
        result = ephemeral_output_dir()
        assert result == tmp_path / ".vox"
        assert result.is_dir()

    def test_idempotent(self, tmp_path: Path) -> None:
        os.chdir(tmp_path)
        first = ephemeral_output_dir()
        second = ephemeral_output_dir()
        assert first == second
        assert first.is_dir()


class TestCleanEphemeral:
    def test_deletes_all_files(self, tmp_path: Path) -> None:
        os.chdir(tmp_path)
        eph = tmp_path / ".vox"
        eph.mkdir()
        (eph / "a.mp3").write_text("audio")
        (eph / "b.mp3").write_text("audio")

        deleted = clean_ephemeral()

        assert deleted == 2
        assert list(eph.iterdir()) == []

    def test_keeps_specified_file(self, tmp_path: Path) -> None:
        os.chdir(tmp_path)
        eph = tmp_path / ".vox"
        eph.mkdir()
        keep = eph / "playing.mp3"
        keep.write_text("audio")
        (eph / "old.mp3").write_text("audio")

        deleted = clean_ephemeral(keep=keep)

        assert deleted == 1
        assert keep.exists()
        assert not (eph / "old.mp3").exists()

    def test_preserves_non_mp3_files(self, tmp_path: Path) -> None:
        os.chdir(tmp_path)
        eph = tmp_path / ".vox"
        eph.mkdir()
        config = eph / "config.md"
        config.write_text("---\nnotify: y\n---\n")
        (eph / "old.mp3").write_text("audio")

        deleted = clean_ephemeral()

        assert deleted == 1
        assert config.exists()
        assert not (eph / "old.mp3").exists()

    def test_no_directory_returns_zero(self, tmp_path: Path) -> None:
        os.chdir(tmp_path)
        assert clean_ephemeral() == 0

    def test_empty_directory_returns_zero(self, tmp_path: Path) -> None:
        os.chdir(tmp_path)
        (tmp_path / ".vox").mkdir()
        assert clean_ephemeral() == 0


class TestRemoveEphemeralDir:
    def test_removes_directory(self, tmp_path: Path) -> None:
        os.chdir(tmp_path)
        eph = tmp_path / ".vox"
        eph.mkdir()
        (eph / "file.mp3").write_text("audio")

        result = remove_ephemeral_dir()

        assert result is True
        assert not eph.exists()

    def test_no_directory_returns_false(self, tmp_path: Path) -> None:
        os.chdir(tmp_path)
        assert remove_ephemeral_dir() is False
