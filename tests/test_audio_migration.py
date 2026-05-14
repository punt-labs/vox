"""Tests for punt_vox.audio_migration."""

from __future__ import annotations

from pathlib import Path

from punt_vox.audio_migration import AudioMigration


class TestAudioMigration:
    def test_scan_no_source_dir(self, tmp_path: Path) -> None:
        """scan() returns False when source does not exist."""
        src = tmp_path / "nonexistent"
        dst = tmp_path / "dest"
        migration = AudioMigration(src, dst)
        assert migration.scan() is False

    def test_scan_empty_source(self, tmp_path: Path) -> None:
        """scan() returns False when source is empty."""
        src = tmp_path / "src"
        src.mkdir()
        dst = tmp_path / "dest"
        migration = AudioMigration(src, dst)
        assert migration.scan() is False

    def test_scan_finds_files(self, tmp_path: Path) -> None:
        """scan() returns True when files are present."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "track.mp3").write_bytes(b"\xff" * 100)
        dst = tmp_path / "dest"
        migration = AudioMigration(src, dst)
        assert migration.scan() is True

    def test_preview_dry_run(self, tmp_path: Path, capsys: object) -> None:
        """preview() prints the plan without moving files."""
        import _pytest.capture

        assert isinstance(capsys, _pytest.capture.CaptureFixture)
        src = tmp_path / "src"
        src.mkdir()
        (src / "a.mp3").write_bytes(b"\xff" * 50)
        dst = tmp_path / "dest"

        migration = AudioMigration(src, dst)
        migration.scan()
        migration.preview()

        captured = capsys.readouterr()
        assert "dry run" in captured.out
        assert "a.mp3" in captured.out
        # File was not moved
        assert (src / "a.mp3").exists()

    def test_execute_moves_files(self, tmp_path: Path) -> None:
        """execute() moves files from source to destination."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "b.mp3").write_bytes(b"\xff" * 80)
        dst = tmp_path / "dest"

        migration = AudioMigration(src, dst)
        migration.scan()
        migration.execute()

        assert (dst / "b.mp3").exists()
        assert not (src / "b.mp3").exists()

    def test_music_dir_renamed_to_tracks(self, tmp_path: Path) -> None:
        """Files under music/ are mapped to tracks/ in destination."""
        src = tmp_path / "src"
        music = src / "music"
        music.mkdir(parents=True)
        (music / "song.mp3").write_bytes(b"\xff" * 40)
        dst = tmp_path / "dest"

        migration = AudioMigration(src, dst)
        migration.scan()
        migration.execute()

        assert (dst / "tracks" / "song.mp3").exists()

    def test_duplicate_skipped(self, tmp_path: Path) -> None:
        """Files already present with matching size+mtime are skipped."""
        src = tmp_path / "src"
        src.mkdir()
        src_file = src / "dup.mp3"
        src_file.write_bytes(b"\xff" * 60)

        dst = tmp_path / "dest"
        dst.mkdir()
        dst_file = dst / "dup.mp3"
        # Copy with same content and set same mtime
        import shutil

        shutil.copy2(str(src_file), str(dst_file))

        migration = AudioMigration(src, dst)
        migration.scan()
        # Only the skipped entry, no pairs to move
        migration.execute()
        # Both files still exist
        assert src_file.exists()
        assert dst_file.exists()
