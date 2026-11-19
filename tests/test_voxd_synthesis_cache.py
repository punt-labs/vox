"""Anonymous cache lookup and billing-isolation boundaries."""

from pathlib import Path
from unittest.mock import patch

import pytest

from punt_vox.cache import CacheKey
from punt_vox.types_synthesis import SynthesisSpec
from punt_vox.voxd.synthesis_cache import SynthesisCache


class TestSynthesisCache:
    """A lookup reads fresh state each time and never crosses billing scopes."""

    def test_miss_then_hit(self, tmp_path: Path) -> None:
        key = CacheKey("hello", "", "openai")
        lookup = SynthesisCache.for_request(
            "hello", SynthesisSpec(provider="openai"), 5
        )
        with patch("punt_vox.cache.CACHE_DIR", tmp_path):
            assert lookup.outcome() is None
            path = key.path_in(tmp_path)
            path.write_bytes(b"audio")
            outcome = lookup.outcome()
        assert outcome is not None
        assert outcome.path == path
        assert outcome.cached is True

    def test_empty_entry_is_a_miss(self, tmp_path: Path) -> None:
        key = CacheKey("hello", "", "openai")
        path = key.path_in(tmp_path)
        path.touch()
        with patch("punt_vox.cache.CACHE_DIR", tmp_path):
            assert SynthesisCache(key, SynthesisSpec(), 5).outcome() is None
        assert not path.exists()

    def test_override_never_reads_cache(self) -> None:
        spec = SynthesisSpec(api_key="scoped")
        lookup = SynthesisCache(CacheKey("hello", "", "openai"), spec, 5)
        with (
            patch("punt_vox.voxd.synthesis_cache.cache_get") as get,
            patch("punt_vox.voxd.synthesis_cache.cache_put") as put,
        ):
            assert lookup.outcome() is None
            assert lookup.outcome() is None
            assert lookup.store(Path("unused")) is None
        get.assert_not_called()
        put.assert_not_called()

    def test_cache_error_propagates(self) -> None:
        lookup = SynthesisCache(CacheKey("hello", "", "openai"), SynthesisSpec(), 5)
        with (
            patch(
                "punt_vox.voxd.synthesis_cache.cache_get",
                side_effect=PermissionError("cache denied"),
            ),
            pytest.raises(PermissionError, match="cache denied"),
        ):
            lookup.outcome()

    def test_store_then_lookup(self, tmp_path: Path) -> None:
        lookup = SynthesisCache.for_request(
            "hello", SynthesisSpec(provider="openai"), 5
        )
        source = tmp_path / "source.mp3"
        source.write_bytes(b"audio")
        with patch("punt_vox.cache.CACHE_DIR", tmp_path / "cache"):
            stored = lookup.store(source)
            outcome = lookup.outcome()
        assert outcome is not None
        assert outcome.path == stored
        assert outcome.path.read_bytes() == b"audio"

    def test_store_empty_source_is_a_miss(self, tmp_path: Path) -> None:
        lookup = SynthesisCache.for_request("hello", SynthesisSpec(), 5)
        source = tmp_path / "empty.mp3"
        source.touch()
        with patch("punt_vox.cache.CACHE_DIR", tmp_path / "cache"):
            assert lookup.store(source) is None
            assert lookup.outcome() is None
