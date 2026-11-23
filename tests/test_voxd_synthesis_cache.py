"""Anonymous cache lookup and billing-isolation boundaries."""

import asyncio
import shutil
import threading
from pathlib import Path
from typing import BinaryIO
from unittest.mock import patch

import pytest

from punt_vox.cache import CacheKey
from punt_vox.types_synthesis import SynthesisSpec
from punt_vox.voxd.synthesis_cache import SynthesisCache
from punt_vox.voxd.synthesis_result import SynthesisOutcome


@pytest.mark.parametrize("copy_fails", [False, True])
async def test_cancelled_cache_copy_is_released(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, *, copy_fails: bool
) -> None:
    cached = tmp_path / "cached.mp3"
    cached.write_bytes(b"audio")
    loop = asyncio.get_running_loop()
    started: asyncio.Future[Path] = loop.create_future()
    resume = threading.Event()
    collected = asyncio.Event()
    copy = shutil.copyfileobj
    collect = SynthesisOutcome._discard_unclaimed  # pyright: ignore[reportPrivateUsage]

    def delayed_copy(source: BinaryIO, dest: BinaryIO) -> None:
        loop.call_soon_threadsafe(started.set_result, Path(dest.name))
        if not resume.wait(timeout=5):
            raise TimeoutError("cache copy did not resume")
        if copy_fails:
            raise OSError("cache copy denied")
        copy(source, dest)

    def collect_outcome(work: asyncio.Future[SynthesisOutcome | None]) -> None:
        collect(work)
        collected.set()

    with (
        patch("punt_vox.voxd.synthesis_result.shutil.copyfileobj", delayed_copy),
        patch.object(SynthesisOutcome, "_discard_unclaimed", collect_outcome),
    ):
        request = asyncio.create_task(SynthesisOutcome.from_cache(cached))
        try:
            snapshot = await asyncio.wait_for(started, timeout=2)
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request
            assert snapshot.exists()
        finally:
            resume.set()
        await asyncio.wait_for(collected.wait(), timeout=2)

    assert not snapshot.exists()
    assert cached.read_bytes() == b"audio"
    assert (
        "Abandoned cache copy failed: cache copy denied" in caplog.text
    ) == copy_fails


class TestSynthesisCache:
    """A lookup reads fresh state each time and never crosses billing scopes."""

    async def test_miss_then_hit(self, tmp_path: Path) -> None:
        key = CacheKey("hello", "", "openai")
        lookup = SynthesisCache.for_request(
            "hello", SynthesisSpec(provider="openai"), 5
        )
        with patch("punt_vox.cache.CACHE_DIR", tmp_path):
            assert await lookup.outcome() is None
            path = key.path_in(tmp_path)
            path.write_bytes(b"audio")
            outcome = await lookup.outcome()
        assert outcome is not None
        try:
            assert outcome.path != path
            assert outcome.path.read_bytes() == b"audio"
            assert outcome.cached is True
        finally:
            outcome.discard()
        assert path.read_bytes() == b"audio"

    async def test_empty_entry_is_a_miss(self, tmp_path: Path) -> None:
        key = CacheKey("hello", "", "openai")
        path = key.path_in(tmp_path)
        path.touch()
        with patch("punt_vox.cache.CACHE_DIR", tmp_path):
            assert await SynthesisCache(key, SynthesisSpec(), 5).outcome() is None
        assert not path.exists()

    async def test_override_never_reads_cache(self) -> None:
        spec = SynthesisSpec(api_key="scoped")
        lookup = SynthesisCache(CacheKey("hello", "", "openai"), spec, 5)
        with (
            patch("punt_vox.voxd.synthesis_cache.cache_get") as get,
            patch("punt_vox.voxd.synthesis_cache.cache_put") as put,
        ):
            assert await lookup.outcome() is None
            assert await lookup.outcome() is None
            assert lookup.store(Path("unused")) is None
        get.assert_not_called()
        put.assert_not_called()

    async def test_cache_error_propagates(self) -> None:
        lookup = SynthesisCache(CacheKey("hello", "", "openai"), SynthesisSpec(), 5)
        with (
            patch(
                "punt_vox.voxd.synthesis_cache.cache_get",
                side_effect=PermissionError("cache denied"),
            ),
            pytest.raises(PermissionError, match="cache denied"),
        ):
            await lookup.outcome()

    async def test_store_then_lookup(self, tmp_path: Path) -> None:
        lookup = SynthesisCache.for_request(
            "hello", SynthesisSpec(provider="openai"), 5
        )
        source = tmp_path / "source.mp3"
        source.write_bytes(b"audio")
        with patch("punt_vox.cache.CACHE_DIR", tmp_path / "cache"):
            stored = lookup.store(source)
            outcome = await lookup.outcome()
        assert outcome is not None
        try:
            assert outcome.path != stored
            assert outcome.path.read_bytes() == b"audio"
        finally:
            outcome.discard()

    async def test_store_empty_source_is_a_miss(self, tmp_path: Path) -> None:
        lookup = SynthesisCache.for_request("hello", SynthesisSpec(), 5)
        source = tmp_path / "empty.mp3"
        source.touch()
        with patch("punt_vox.cache.CACHE_DIR", tmp_path / "cache"):
            assert lookup.store(source) is None
            assert await lookup.outcome() is None
