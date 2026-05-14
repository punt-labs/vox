"""Tests for punt_vox.voxd.music.generator -- find_track and track lookup."""

from __future__ import annotations

from pathlib import Path

from punt_vox.voxd.music.generator import TrackGenerator

__all__: list[str] = []


class TestFindTrack:
    """TrackGenerator.find_track locates existing tracks by name."""

    def test_find_existing_track(self, tmp_path: Path) -> None:
        music_dir = tmp_path / "music"
        music_dir.mkdir()
        track = music_dir / "my_focus.mp3"
        track.write_bytes(b"fake-music")

        gen = TrackGenerator(music_dir)
        result = gen.find_track("my focus")

        assert result == track

    def test_find_nonexistent_track(self, tmp_path: Path) -> None:
        music_dir = tmp_path / "music"
        music_dir.mkdir()

        gen = TrackGenerator(music_dir)
        result = gen.find_track("does not exist")

        assert result is None

    def test_find_empty_name(self, tmp_path: Path) -> None:
        gen = TrackGenerator(tmp_path)
        result = gen.find_track("")

        assert result is None

    def test_find_name_slugifies_to_empty(self, tmp_path: Path) -> None:
        gen = TrackGenerator(tmp_path)
        result = gen.find_track("---")

        assert result is None
