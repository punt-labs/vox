"""Anonymous cache lookup and billing-isolation boundaries."""

import asyncio
import shutil
import tempfile
import threading
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO
from unittest.mock import patch

import pytest

from punt_vox.cache import CacheKey
from punt_vox.types_synthesis import SynthesisSpec
from punt_vox.voxd.synthesis_cache import SynthesisCache
from punt_vox.voxd.synthesis_result import SynthesisOutcome


@pytest.mark.parametrize("result", ["success", "error", "evicted"])
async def test_cancelled_cache_copy_is_released(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, result: str
) -> None:
    cached = tmp_path / "cached.mp3"
    cached.write_bytes(b"audio")
    loop = asyncio.get_running_loop()
    started: asyncio.Future[Path] = loop.create_future()
    resume = threading.Event()
    collected: asyncio.Future[asyncio.Future[SynthesisOutcome | None]] = (
        loop.create_future()
    )
    copy = shutil.copyfileobj
    copy_cached = SynthesisOutcome._copy_cached  # pyright: ignore[reportPrivateUsage]
    collect = SynthesisOutcome._discard_unclaimed  # pyright: ignore[reportPrivateUsage]

    def wait_for_cancel(path: Path) -> None:
        loop.call_soon_threadsafe(started.set_result, path)
        if not resume.wait(timeout=5):
            raise TimeoutError("cache copy did not resume")

    def delayed_copy(source: BinaryIO, dest: BinaryIO) -> None:
        wait_for_cancel(Path(dest.name))
        if result == "error":
            raise OSError("cache copy denied")
        copy(source, dest)

    def copy_after_eviction(path: Path) -> SynthesisOutcome | None:
        if result == "evicted":
            wait_for_cancel(path)
        return copy_cached(path)

    def collect_outcome(work: asyncio.Future[SynthesisOutcome | None]) -> None:
        collect(work)
        collected.set_result(work)

    with (
        patch("punt_vox.voxd.synthesis_result.shutil.copyfileobj", delayed_copy),
        patch.object(SynthesisOutcome, "_discard_unclaimed", collect_outcome),
        patch.object(SynthesisOutcome, "_copy_cached", copy_after_eviction),
    ):
        request = asyncio.create_task(SynthesisOutcome.from_cache(cached))
        try:
            in_progress = await asyncio.wait_for(started, timeout=2)
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request
            assert in_progress.exists()
            if result == "evicted":
                cached.unlink()
        finally:
            resume.set()
        completed = await asyncio.wait_for(collected, timeout=2)

    if result == "evicted":
        assert completed.result() is None
    else:
        assert not in_progress.exists()
        assert cached.read_bytes() == b"audio"
    warnings = [
        message
        for message in caplog.messages
        if message.startswith("Abandoned cache copy failed:")
    ]
    assert warnings == (
        ["Abandoned cache copy failed: cache copy denied"] if result == "error" else []
    )


@pytest.mark.parametrize("fail_on_close", [False, True])
async def test_snapshot_errors_leave_no_temp_audio(
    tmp_path: Path, *, fail_on_close: bool
) -> None:
    cached = tmp_path / "cached.mp3"
    cached.write_bytes(b"audio")
    make_temp = tempfile.NamedTemporaryFile

    @contextmanager
    def failing_temp(*, suffix: str, delete: bool) -> Generator[object]:
        if not fail_on_close:
            raise OSError("cannot open temp audio")
        with make_temp(dir=tmp_path, suffix=suffix, delete=delete) as dest:
            yield dest
        raise OSError("cannot close temp audio")

    with (
        patch(
            "punt_vox.voxd.synthesis_result.tempfile.NamedTemporaryFile", failing_temp
        ),
        pytest.raises(OSError, match=r"cannot (open|close) temp audio"),
    ):
        await SynthesisOutcome.from_cache(cached)

    assert list(tmp_path.iterdir()) == [cached]
    assert cached.read_bytes() == b"audio"


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
