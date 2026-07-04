"""Tests for punt_vox.voxd.music.generator -- find_track and track lookup."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from punt_vox.voxd.music.generator import TrackGenerator

if TYPE_CHECKING:
    import pytest

__all__: list[str] = []


class TestGenerateFillsPool:
    """generate() lazily fills a pool, passing variation = current pool size."""

    def test_twelve_generations_use_distinct_variation_indices(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        variations: list[int | None] = []

        def fake_prompt(
            vibe: str | None,
            vibe_tags: str | None,
            style: str | None,
            hour: int,
            signals: list[str],
            variation: int | None = None,
        ) -> str:
            variations.append(variation)
            return "prompt"

        async def fake_generate_track(prompt: str, dur: int, out: Path) -> None:
            out.write_bytes(b"x")  # never hits the real ElevenLabs API

        class FakeProvider:
            def __new__(cls) -> FakeProvider:
                return super().__new__(cls)

            generate_track = staticmethod(fake_generate_track)

        monkeypatch.setattr("punt_vox.music.vibe_to_prompt", fake_prompt)
        monkeypatch.setattr(
            "punt_vox.providers.elevenlabs_music.ElevenLabsMusicProvider",
            FakeProvider,
        )

        gen = TrackGenerator(tmp_path)
        for _ in range(12):
            asyncio.run(gen.generate(("calm", ""), "jazz", ""))

        assert variations == list(range(12))
        assert len(gen.tracks_for(gen.pool_prefix(("calm", "jazz")))) == 12


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


class TestPoolPrefix:
    """TrackGenerator.pool_prefix groups tracks by (vibe, style)."""

    def test_prefix_from_vibe_and_style(self, tmp_path: Path) -> None:
        gen = TrackGenerator(tmp_path)
        assert gen.pool_prefix(("deep focus", "lo-fi")) == "deep_focus_lo_fi_"

    def test_prefix_falls_back_when_empty(self, tmp_path: Path) -> None:
        gen = TrackGenerator(tmp_path)
        assert gen.pool_prefix(("", "")) == "ambient_mix_"

    def test_auto_name_counter_is_deterministically_unique(
        self, tmp_path: Path
    ) -> None:
        # Writing each named file (as generate() would) must make the next
        # call pick the next free counter -- deterministic, never a collision.
        gen = TrackGenerator(tmp_path)
        prefix = gen.pool_prefix(("calm", "jazz"))
        names: list[str] = []
        for _ in range(20):
            name = gen.auto_track_name("calm", "jazz")
            names.append(name)
            (tmp_path / f"{name}.mp3").write_bytes(b"x")

        assert all(n.startswith(prefix) for n in names)
        assert len(set(names)) == 20  # 20 sequential same-minute files, all distinct
        assert names[-1].endswith("_19")  # counter advanced 0..19, deterministically


class TestTracksFor:
    """TrackGenerator.tracks_for enumerates one (vibe, style) pool."""

    def test_groups_matching_prefix_only(self, tmp_path: Path) -> None:
        gen = TrackGenerator(tmp_path)
        prefix = gen.pool_prefix(("calm", "jazz"))
        matching = {tmp_path / f"{prefix}{i}.mp3" for i in range(3)}
        for path in matching:
            path.write_bytes(b"x")
        (tmp_path / "happy_techno_1.mp3").write_bytes(b"x")

        assert set(gen.tracks_for(gen.pool_prefix(("calm", "jazz")))) == matching

    def test_empty_when_dir_missing(self, tmp_path: Path) -> None:
        gen = TrackGenerator(tmp_path / "missing")
        assert gen.tracks_for(gen.pool_prefix(("calm", "jazz"))) == []

    def test_separate_pool_per_vibe_style(self, tmp_path: Path) -> None:
        gen = TrackGenerator(tmp_path)
        (tmp_path / f"{gen.pool_prefix(('calm', 'jazz'))}a.mp3").write_bytes(b"x")
        (tmp_path / f"{gen.pool_prefix(('calm', 'techno'))}a.mp3").write_bytes(b"x")

        assert len(gen.tracks_for(gen.pool_prefix(("calm", "jazz")))) == 1
        assert len(gen.tracks_for(gen.pool_prefix(("calm", "techno")))) == 1
