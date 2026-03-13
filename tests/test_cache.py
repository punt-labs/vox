"""Tests for MP3 cache (src/punt_vox/cache.py)."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

from punt_vox.cache import (
    CacheInfo,
    cache_clear,
    cache_get,
    cache_key,
    cache_put,
    cache_status,
)


def _fake_mp3(path: Path, size: int = 100) -> None:
    """Write a fake MP3 file with the given size."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xff" * size)


# ---------------------------------------------------------------------------
# cache_key tests
# ---------------------------------------------------------------------------


class TestCacheKey:
    def test_deterministic(self) -> None:
        k1 = cache_key("hello", "matilda", "elevenlabs")
        k2 = cache_key("hello", "matilda", "elevenlabs")
        assert k1 == k2

    def test_ends_with_mp3(self) -> None:
        assert cache_key("text", "voice", "provider").endswith(".mp3")

    def test_hex_prefix_length(self) -> None:
        key = cache_key("text", "voice", "provider")
        stem = key.removesuffix(".mp3")
        assert len(stem) == 32
        # All hex chars
        int(stem, 16)

    def test_different_text_different_key(self) -> None:
        assert cache_key("alpha", "v", "p") != cache_key("bravo", "v", "p")

    def test_different_voice_different_key(self) -> None:
        assert cache_key("t", "matilda", "p") != cache_key("t", "roger", "p")

    def test_different_provider_different_key(self) -> None:
        assert cache_key("t", "v", "elevenlabs") != cache_key("t", "v", "polly")

    def test_none_voice_and_provider(self) -> None:
        key = cache_key("hello", None, None)
        assert key.endswith(".mp3")

    def test_separator_prevents_collision(self) -> None:
        # "ab" + "c" vs "a" + "bc" must differ
        assert cache_key("ab", "c", "p") != cache_key("a", "bc", "p")


# ---------------------------------------------------------------------------
# cache_get tests
# ---------------------------------------------------------------------------


class TestCacheGet:
    def test_miss_returns_none(self, tmp_path: Path) -> None:
        with patch("punt_vox.cache.CACHE_DIR", tmp_path):
            assert cache_get("nonexistent", None, None) is None

    def test_hit_returns_path(self, tmp_path: Path) -> None:
        with patch("punt_vox.cache.CACHE_DIR", tmp_path):
            key = cache_key("hello", "matilda", "elevenlabs")
            cached = tmp_path / key
            _fake_mp3(cached)
            result = cache_get("hello", "matilda", "elevenlabs")
            assert result is not None
            assert result == cached

    def test_hit_updates_mtime(self, tmp_path: Path) -> None:
        with patch("punt_vox.cache.CACHE_DIR", tmp_path):
            key = cache_key("hello", None, None)
            cached = tmp_path / key
            _fake_mp3(cached)
            # Set mtime to the past
            old_time = time.time() - 3600
            import os

            os.utime(cached, (old_time, old_time))
            old_mtime = cached.stat().st_mtime

            cache_get("hello", None, None)
            new_mtime = cached.stat().st_mtime
            assert new_mtime > old_mtime

    def test_empty_file_treated_as_miss(self, tmp_path: Path) -> None:
        with patch("punt_vox.cache.CACHE_DIR", tmp_path):
            key = cache_key("hello", None, None)
            cached = tmp_path / key
            cached.write_bytes(b"")
            assert cache_get("hello", None, None) is None
            # Empty file should be deleted
            assert not cached.exists()


# ---------------------------------------------------------------------------
# cache_put tests
# ---------------------------------------------------------------------------


class TestCachePut:
    def test_copies_file(self, tmp_path: Path) -> None:
        cache_dir = tmp_path / "cache"
        source = tmp_path / "source.mp3"
        _fake_mp3(source, size=200)

        with patch("punt_vox.cache.CACHE_DIR", cache_dir):
            result = cache_put("hello", "matilda", "elevenlabs", source)

        assert result is not None
        assert result.exists()
        assert result.stat().st_size == 200
        assert result.parent == cache_dir

    def test_missing_source_returns_none(self, tmp_path: Path) -> None:
        cache_dir = tmp_path / "cache"
        source = tmp_path / "nonexistent.mp3"

        with patch("punt_vox.cache.CACHE_DIR", cache_dir):
            assert cache_put("hello", None, None, source) is None

    def test_empty_source_returns_none(self, tmp_path: Path) -> None:
        cache_dir = tmp_path / "cache"
        source = tmp_path / "empty.mp3"
        source.write_bytes(b"")

        with patch("punt_vox.cache.CACHE_DIR", cache_dir):
            assert cache_put("hello", None, None, source) is None

    def test_creates_cache_dir(self, tmp_path: Path) -> None:
        cache_dir = tmp_path / "new_cache_dir"
        source = tmp_path / "source.mp3"
        _fake_mp3(source)

        with patch("punt_vox.cache.CACHE_DIR", cache_dir):
            result = cache_put("hello", None, None, source)

        assert result is not None
        assert cache_dir.exists()


# ---------------------------------------------------------------------------
# eviction tests
# ---------------------------------------------------------------------------


class TestEviction:
    def test_evicts_oldest_when_over_max(self, tmp_path: Path) -> None:
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir(parents=True)
        source = tmp_path / "source.mp3"
        _fake_mp3(source)

        with (
            patch("punt_vox.cache.CACHE_DIR", cache_dir),
            patch("punt_vox.cache.MAX_ENTRIES", 3),
        ):
            # Fill cache with 3 entries
            for i in range(3):
                f = cache_dir / f"existing_{i:04d}.mp3"
                _fake_mp3(f)
                # Stagger mtimes so eviction order is deterministic
                import os

                os.utime(f, (1000 + i, 1000 + i))

            # Adding one more should evict the oldest
            cache_put("new_entry", None, None, source)

            mp3s = list(cache_dir.glob("*.mp3"))
            assert len(mp3s) <= 3
            # The oldest file (existing_0000) should be gone
            assert not (cache_dir / "existing_0000.mp3").exists()


# ---------------------------------------------------------------------------
# cache_clear tests
# ---------------------------------------------------------------------------


class TestCacheClear:
    def test_clear_returns_count(self, tmp_path: Path) -> None:
        with patch("punt_vox.cache.CACHE_DIR", tmp_path):
            for i in range(5):
                _fake_mp3(tmp_path / f"file_{i}.mp3")
            count = cache_clear()
            assert count == 5

    def test_clear_deletes_files(self, tmp_path: Path) -> None:
        with patch("punt_vox.cache.CACHE_DIR", tmp_path):
            for i in range(3):
                _fake_mp3(tmp_path / f"file_{i}.mp3")
            cache_clear()
            assert list(tmp_path.glob("*.mp3")) == []

    def test_clear_empty_cache(self, tmp_path: Path) -> None:
        with patch("punt_vox.cache.CACHE_DIR", tmp_path):
            assert cache_clear() == 0

    def test_clear_nonexistent_dir(self, tmp_path: Path) -> None:
        nonexistent = tmp_path / "nope"
        with patch("punt_vox.cache.CACHE_DIR", nonexistent):
            assert cache_clear() == 0


# ---------------------------------------------------------------------------
# cache_status tests
# ---------------------------------------------------------------------------


class TestCacheStatus:
    def test_status_empty(self, tmp_path: Path) -> None:
        with patch("punt_vox.cache.CACHE_DIR", tmp_path):
            info = cache_status()
            assert info.entries == 0
            assert info.size_bytes == 0
            assert info.path == tmp_path

    def test_status_with_files(self, tmp_path: Path) -> None:
        with patch("punt_vox.cache.CACHE_DIR", tmp_path):
            _fake_mp3(tmp_path / "a.mp3", size=100)
            _fake_mp3(tmp_path / "b.mp3", size=200)
            info = cache_status()
            assert info.entries == 2
            assert info.size_bytes == 300
            assert isinstance(info, CacheInfo)

    def test_status_nonexistent_dir(self, tmp_path: Path) -> None:
        nonexistent = tmp_path / "nope"
        with patch("punt_vox.cache.CACHE_DIR", nonexistent):
            info = cache_status()
            assert info.entries == 0
            assert info.size_bytes == 0
