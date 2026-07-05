"""Tests for punt_vox.voxd.music.generator -- generation, naming, and lookup.

The generator is exercised against the real FilesystemTrackStore so the
generator->store integration is covered end to end.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from punt_vox.music_prompts import POOL_SIZE, PromptSet
from punt_vox.voxd.music.generator import TrackGenerator
from punt_vox.voxd.music.store import FilesystemTrackStore

if TYPE_CHECKING:
    import pytest

__all__: list[str] = []


def _gen(output_dir: Path) -> TrackGenerator:
    """Build a TrackGenerator backed by a real filesystem store."""
    return TrackGenerator(FilesystemTrackStore(output_dir))


class TestGenerateFillsPool:
    """generate() fills a pool, drawing variation i for the i-th track."""

    def test_twelve_generations_use_variation_i_for_track_i(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sent: list[str] = []

        async def fake_generate_track(prompt: str, dur: int, out: Path) -> None:
            sent.append(prompt)
            out.write_bytes(b"x")  # never hits the real ElevenLabs API

        class FakeProvider:
            def __new__(cls) -> FakeProvider:
                return super().__new__(cls)

            generate_track = staticmethod(fake_generate_track)

        monkeypatch.setattr(
            "punt_vox.providers.elevenlabs_music.ElevenLabsMusicProvider",
            FakeProvider,
        )

        prompts = PromptSet.from_agent("BASE", [f"var{i}" for i in range(POOL_SIZE)])
        gen = _gen(tmp_path)
        for _ in range(POOL_SIZE):
            asyncio.run(gen.generate(("calm", ""), "jazz", "", prompts))

        assert sent == [f"BASE var{i}" for i in range(POOL_SIZE)]
        assert len(set(sent)) == POOL_SIZE
        assert len(gen.tracks_for(gen.pool_prefix(("calm", "jazz")))) == POOL_SIZE

    def test_fallback_prompt_used_when_no_agent_prompts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sent: list[str] = []

        async def fake_generate_track(prompt: str, dur: int, out: Path) -> None:
            sent.append(prompt)
            out.write_bytes(b"x")

        class FakeProvider:
            def __new__(cls) -> FakeProvider:
                return super().__new__(cls)

            generate_track = staticmethod(fake_generate_track)

        monkeypatch.setattr(
            "punt_vox.providers.elevenlabs_music.ElevenLabsMusicProvider",
            FakeProvider,
        )

        fallback = PromptSet.fallback("jazz", "calm")
        gen = _gen(tmp_path)
        asyncio.run(gen.generate(("calm", ""), "jazz", "", fallback))

        assert sent == ["jazz music, calm. instrumental, loopable."]


class TestFindTrack:
    """TrackGenerator.find_track locates existing tracks by name."""

    def test_find_existing_track(self, tmp_path: Path) -> None:
        music_dir = tmp_path / "music"
        music_dir.mkdir()
        track = music_dir / "my_focus.mp3"
        track.write_bytes(b"fake-music")

        gen = _gen(music_dir)
        result = gen.find_track("my focus")

        assert result == track

    def test_find_nonexistent_track(self, tmp_path: Path) -> None:
        music_dir = tmp_path / "music"
        music_dir.mkdir()

        gen = _gen(music_dir)
        result = gen.find_track("does not exist")

        assert result is None

    def test_find_empty_name(self, tmp_path: Path) -> None:
        gen = _gen(tmp_path)
        result = gen.find_track("")

        assert result is None

    def test_find_name_slugifies_to_empty(self, tmp_path: Path) -> None:
        gen = _gen(tmp_path)
        result = gen.find_track("---")

        assert result is None


class TestPoolPrefix:
    """TrackGenerator.pool_prefix groups tracks by (vibe, style)."""

    def test_prefix_from_vibe_and_style(self, tmp_path: Path) -> None:
        gen = _gen(tmp_path)
        assert gen.pool_prefix(("deep focus", "lo-fi")) == "deep_focus_lo_fi_"

    def test_prefix_falls_back_when_empty(self, tmp_path: Path) -> None:
        gen = _gen(tmp_path)
        assert gen.pool_prefix(("", "")) == "ambient_mix_"

    def test_auto_name_counter_is_deterministically_unique(
        self, tmp_path: Path
    ) -> None:
        # Writing each named file (as generate() would) must make the next
        # call pick the next free counter -- deterministic, never a collision.
        gen = _gen(tmp_path)
        prefix = gen.pool_prefix(("calm", "jazz"))
        names: list[str] = []
        for _ in range(20):
            name = gen.auto_track_name(gen.pool_prefix(("calm", "jazz")))
            names.append(name)
            (tmp_path / f"{name}.mp3").write_bytes(b"x")

        assert all(n.startswith(prefix) for n in names)
        assert len(set(names)) == 20  # 20 sequential same-minute files, all distinct
        assert names[-1].endswith("_19")  # counter advanced 0..19, deterministically


class TestTracksFor:
    """TrackGenerator.tracks_for enumerates one (vibe, style) pool."""

    def test_groups_matching_prefix_only(self, tmp_path: Path) -> None:
        gen = _gen(tmp_path)
        prefix = gen.pool_prefix(("calm", "jazz"))
        matching = {tmp_path / f"{prefix}{i}.mp3" for i in range(3)}
        for path in matching:
            path.write_bytes(b"x")
        (tmp_path / "happy_techno_1.mp3").write_bytes(b"x")

        assert set(gen.tracks_for(gen.pool_prefix(("calm", "jazz")))) == matching

    def test_empty_when_dir_missing(self, tmp_path: Path) -> None:
        gen = _gen(tmp_path / "missing")
        assert gen.tracks_for(gen.pool_prefix(("calm", "jazz"))) == ()

    def test_separate_pool_per_vibe_style(self, tmp_path: Path) -> None:
        gen = _gen(tmp_path)
        (tmp_path / f"{gen.pool_prefix(('calm', 'jazz'))}a.mp3").write_bytes(b"x")
        (tmp_path / f"{gen.pool_prefix(('calm', 'techno'))}a.mp3").write_bytes(b"x")

        assert len(gen.tracks_for(gen.pool_prefix(("calm", "jazz")))) == 1
        assert len(gen.tracks_for(gen.pool_prefix(("calm", "techno")))) == 1


class TestSlugify:
    """TrackGenerator.slugify normalizes text for filenames (migrated)."""

    def test_simple(self) -> None:
        assert TrackGenerator.slugify("hello world") == "hello_world"

    def test_special_chars(self) -> None:
        assert TrackGenerator.slugify("a!@#b") == "a_b"

    def test_max_len(self) -> None:
        assert len(TrackGenerator.slugify("a" * 100, max_len=10)) == 10

    def test_empty(self) -> None:
        assert TrackGenerator.slugify("") == ""

    def test_strips_leading_trailing_underscores(self) -> None:
        assert TrackGenerator.slugify("  hello  ") == "hello"

    def test_lowercase(self) -> None:
        assert TrackGenerator.slugify("Hello World") == "hello_world"


class TestAutoTrackName:
    """auto_track_name derives <vibe>_<style>_YYYYMMDD_HHMM_<counter> (migrated)."""

    def test_with_vibe_and_style(self, tmp_path: Path) -> None:
        name = _gen(tmp_path).auto_track_name(
            TrackGenerator.pool_prefix(("happy", "techno"))
        )
        assert name.startswith("happy_techno_")
        parts = name.split("_")
        assert len(parts[-3]) == 8  # YYYYMMDD
        assert len(parts[-2]) == 4  # HHMM
        assert parts[-1] == "0"  # first free counter in an empty pool

    def test_no_vibe_uses_ambient(self, tmp_path: Path) -> None:
        assert (
            _gen(tmp_path)
            .auto_track_name(TrackGenerator.pool_prefix(("", "")))
            .startswith("ambient_mix_")
        )

    def test_no_style_uses_mix(self, tmp_path: Path) -> None:
        assert (
            _gen(tmp_path)
            .auto_track_name(TrackGenerator.pool_prefix(("chill", "")))
            .startswith("chill_mix_")
        )


class TestCanGenerate:
    """can_generate reflects whether a provider API key is configured."""

    def test_true_when_key_set(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ELEVENLABS_API_KEY", "sk-real")
        assert _gen(tmp_path).can_generate() is True

    def test_false_when_key_absent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
        assert _gen(tmp_path).can_generate() is False


class TestListTracks:
    """TrackGenerator.list_tracks maps stored metadata to MusicTrack (migrated)."""

    def test_empty_pool_lists_nothing(self, tmp_path: Path) -> None:
        assert _gen(tmp_path).list_tracks() == []

    def test_lists_mp3_files_as_music_tracks(self, tmp_path: Path) -> None:
        (tmp_path / "alpha.mp3").write_bytes(b"fake-audio-1")
        (tmp_path / "beta.mp3").write_bytes(b"fake-audio-2")
        (tmp_path / "readme.txt").write_bytes(b"not audio")

        tracks = _gen(tmp_path).list_tracks()

        assert [t.name for t in tracks] == ["alpha", "beta"]  # sorted, .mp3 only
        assert all(t.size_bytes > 0 for t in tracks)
        assert all(t.modified > 0 for t in tracks)
