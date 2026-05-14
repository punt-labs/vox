"""Tests for punt_vox.voxd.track_generator."""
# pyright: reportPrivateUsage=false

from __future__ import annotations

from pathlib import Path

from punt_vox.voxd.music.generator import TrackGenerator


class TestSlugify:
    """TrackGenerator.slugify normalizes text for filenames."""

    def test_simple(self) -> None:
        assert TrackGenerator.slugify("hello world") == "hello_world"

    def test_special_chars(self) -> None:
        assert TrackGenerator.slugify("a!@#b") == "a_b"

    def test_max_len(self) -> None:
        result = TrackGenerator.slugify("a" * 100, max_len=10)
        assert len(result) == 10

    def test_empty(self) -> None:
        assert TrackGenerator.slugify("") == ""

    def test_strips_leading_trailing_underscores(self) -> None:
        assert TrackGenerator.slugify("  hello  ") == "hello"

    def test_lowercase(self) -> None:
        assert TrackGenerator.slugify("Hello World") == "hello_world"


class TestAutoTrackName:
    """TrackGenerator.auto_track_name derives vibe-style-YYYYMMDD-HHMM patterns."""

    def test_with_vibe_and_style(self, tmp_path: Path) -> None:
        gen = TrackGenerator(tmp_path)
        name = gen.auto_track_name("happy", "techno")
        # Name has vibe-style-YYYYMMDD-HHMM structure.
        assert name.startswith("happy-techno-")
        # Suffix is YYYYMMDD-HHMM: 8 digits, dash, 4 digits.
        parts = name.split("-")
        assert len(parts[-2]) == 8  # YYYYMMDD
        assert len(parts[-1]) == 4  # HHMM

    def test_no_vibe_uses_ambient(self, tmp_path: Path) -> None:
        gen = TrackGenerator(tmp_path)
        name = gen.auto_track_name("", "")
        assert name.startswith("ambient-mix-")

    def test_no_style_uses_mix(self, tmp_path: Path) -> None:
        gen = TrackGenerator(tmp_path)
        name = gen.auto_track_name("chill", "")
        assert name.startswith("chill-mix-")


class TestListTracks:
    """TrackGenerator.list_tracks returns metadata for saved .mp3 files."""

    def test_empty_dir(self, tmp_path: Path) -> None:
        gen = TrackGenerator(tmp_path)
        assert gen.list_tracks() == []

    def test_nonexistent_dir(self, tmp_path: Path) -> None:
        gen = TrackGenerator(tmp_path / "nonexistent")
        assert gen.list_tracks() == []

    def test_lists_mp3_files(self, tmp_path: Path) -> None:
        (tmp_path / "alpha.mp3").write_bytes(b"fake-audio-1")
        (tmp_path / "beta.mp3").write_bytes(b"fake-audio-2")
        (tmp_path / "readme.txt").write_bytes(b"not audio")

        gen = TrackGenerator(tmp_path)
        tracks = gen.list_tracks()

        assert len(tracks) == 2
        names = [t["name"] for t in tracks]
        assert names == ["alpha", "beta"]  # sorted
        assert all("size_bytes" in t for t in tracks)
        assert all("modified" in t for t in tracks)
        assert all("path" in t for t in tracks)


class TestOutputDirProperty:
    """TrackGenerator.output_dir exposes the configured directory."""

    def test_returns_configured_dir(self, tmp_path: Path) -> None:
        gen = TrackGenerator(tmp_path / "music")
        assert gen.output_dir == tmp_path / "music"
