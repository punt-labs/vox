"""Tests for PoolFiller -- the scheduler-owned background fill task.

Every test drives the REAL fill task. Generation is faked at the class
level (registering a track in the in-memory store the way a real
generation would), so the sequential fill loop, the stop-at-POOL_SIZE
condition, cancellation, and retargeting are all exercised without a
subprocess or the ElevenLabs API.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable, Coroutine
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest

from music.conftest import FakeTrackStore
from punt_vox.voxd.music.filler import PoolFiller
from punt_vox.voxd.music.generator import TrackGenerator
from punt_vox.voxd.music.pool import POOL_SIZE

__all__: list[str] = []

# A patched TrackGenerator.generate: bound, so its first arg is the generator.
GenerateFn = Callable[
    [TrackGenerator, tuple[str, str], str, str],
    Coroutine[Any, Any, tuple[Any, str]],
]


def _seed(store: FakeTrackStore, vibe: str, style: str, count: int) -> str:
    """Register ``count`` tracks for one pool; return the pool prefix."""
    prefix = TrackGenerator.pool_prefix((vibe, style))
    for i in range(count):
        store.add(f"{prefix}seed{i:02d}")
    return prefix


def _registering_generate(calls: list[tuple[str, str]]) -> GenerateFn:
    """Build a fake ``generate`` that adds a distinct track and records the call.

    Patched at class level so ``self`` binds to the generator; tracks are
    named by the pool's current count, mirroring the real deterministic
    naming so every generated track is unique.
    """

    async def generate(
        self: TrackGenerator, vibe: tuple[str, str], style: str, name: str
    ) -> tuple[Any, str]:
        calls.append((vibe[0], style))
        prefix = TrackGenerator.pool_prefix((vibe[0], style))
        store = cast("FakeTrackStore", self._store)  # the injected fake
        n = len(store.tracks_for(prefix))
        stem = f"{prefix}{n:02d}"
        return store.add(stem), stem

    return generate


async def _drain(filler: PoolFiller) -> None:
    """Await the filler's live task to completion (guards Optional for typing)."""
    task = filler._task
    assert task is not None
    await task


class TestFillToPoolSize:
    """The fill generates up to POOL_SIZE, then the task exits cleanly."""

    def test_fills_to_pool_size_then_stops(self) -> None:
        store = FakeTrackStore()
        _seed(store, "calm", "jazz", 3)
        calls: list[tuple[str, str]] = []
        filler: PoolFiller | None = None

        async def _drive() -> None:
            nonlocal filler
            with (
                patch.object(TrackGenerator, "generate", _registering_generate(calls)),
                patch.object(PoolFiller, "_backoff", AsyncMock()),
            ):
                filler = PoolFiller(TrackGenerator(store))
                filler.ensure_running(("calm", ""), "jazz")
                await _drain(filler)

        asyncio.run(_drive())

        assert filler is not None
        assert len(calls) == POOL_SIZE - 3  # only the shortfall
        assert filler._task is not None
        assert filler._task.done()
        assert not filler._task.cancelled()
        assert len(store.tracks_for(TrackGenerator.pool_prefix(("calm", "jazz")))) == (
            POOL_SIZE
        )


class TestRestartResumesFromDiskCount:
    """turn_on/restart resumes fill from the on-disk count; full pool no-ops."""

    def test_partial_pool_resumes(self) -> None:
        store = FakeTrackStore()
        _seed(store, "calm", "jazz", 5)
        calls: list[tuple[str, str]] = []
        filler: PoolFiller | None = None

        async def _drive() -> None:
            nonlocal filler
            with (
                patch.object(TrackGenerator, "generate", _registering_generate(calls)),
                patch.object(PoolFiller, "_backoff", AsyncMock()),
            ):
                filler = PoolFiller(TrackGenerator(store))
                filler.ensure_running(("calm", ""), "jazz")
                await _drain(filler)

        asyncio.run(_drive())

        assert len(calls) == POOL_SIZE - 5  # 7 generations

    def test_full_pool_starts_no_task(self) -> None:
        store = FakeTrackStore()
        _seed(store, "calm", "jazz", POOL_SIZE)
        calls: list[tuple[str, str]] = []
        filler: PoolFiller | None = None

        async def _drive() -> None:
            nonlocal filler
            with patch.object(TrackGenerator, "generate", _registering_generate(calls)):
                filler = PoolFiller(TrackGenerator(store))
                filler.ensure_running(("calm", ""), "jazz")
                await asyncio.sleep(0)

        asyncio.run(_drive())

        assert filler is not None
        assert not filler.is_running  # full pool -> no fill task spawned
        assert calls == []  # zero generations


class TestCancelStopsGeneration:
    """/music off cancels the fill task with no orphaned generation."""

    def test_cancel_freezes_generation_count(self) -> None:
        store = FakeTrackStore()
        _seed(store, "calm", "jazz", 1)
        started = asyncio.Event()
        release = asyncio.Event()
        calls = 0
        filler: PoolFiller | None = None

        async def blocking_generate(
            gen: TrackGenerator, vibe: tuple[str, str], style: str, name: str
        ) -> tuple[object, str]:
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()  # never released -> cancelled mid-flight
            stem = f"{TrackGenerator.pool_prefix((vibe[0], style))}99"
            return store.add(stem), stem

        async def _drive() -> None:
            nonlocal filler
            with patch.object(TrackGenerator, "generate", blocking_generate):
                filler = PoolFiller(TrackGenerator(store))
                filler.ensure_running(("calm", ""), "jazz")
                await asyncio.wait_for(started.wait(), timeout=2.0)
                task = filler._task
                filler.cancel()
                await asyncio.sleep(0)  # let the CancelledError propagate
                assert task is not None
                with contextlib.suppress(asyncio.CancelledError):
                    await task
                assert task.cancelled()

        asyncio.run(_drive())

        assert calls == 1  # the in-flight generation; no track was ever added
        assert filler is not None
        assert not filler.is_running
        assert len(store.tracks_for(TrackGenerator.pool_prefix(("calm", "jazz")))) == 1


class TestRetargetCancelsOldTask:
    """Retargeting to a new pool cancels the old fill and starts the new one."""

    def test_retarget_switches_pool(self) -> None:
        store = FakeTrackStore()
        started_a = asyncio.Event()
        release = asyncio.Event()
        seen: list[str] = []
        filler: PoolFiller | None = None
        old_task_holder: list[asyncio.Task[None]] = []

        async def blocking_generate(
            gen: TrackGenerator, vibe: tuple[str, str], style: str, name: str
        ) -> tuple[object, str]:
            seen.append(style)
            if style == "jazz":
                started_a.set()
                await release.wait()  # hold pool A's first generation
            prefix = TrackGenerator.pool_prefix((vibe[0], style))
            n = len(store.tracks_for(prefix))
            stem = f"{prefix}{n:02d}"
            return store.add(stem), stem

        async def _drive() -> None:
            nonlocal filler
            with (
                patch.object(TrackGenerator, "generate", blocking_generate),
                patch.object(PoolFiller, "_backoff", AsyncMock()),
            ):
                filler = PoolFiller(TrackGenerator(store))
                filler.ensure_running(("calm", ""), "jazz")
                await asyncio.wait_for(started_a.wait(), timeout=2.0)
                old_task = filler._task
                assert old_task is not None
                old_task_holder.append(old_task)
                filler.ensure_running(("bright", ""), "techno")  # retarget
                await asyncio.sleep(0)
                await _drain(filler)  # new pool fills

        asyncio.run(_drive())

        assert old_task_holder[0].cancelled()  # pool A fill was cancelled
        techno = TrackGenerator.pool_prefix(("bright", "techno"))
        assert len(store.tracks_for(techno)) == POOL_SIZE  # pool B filled


class TestFirstTrackHandshake:
    """await_first_track returns #1 on success and raises on persistent failure."""

    def test_await_first_track_returns_first(self) -> None:
        store = FakeTrackStore()
        calls: list[tuple[str, str]] = []
        result: list[object] = []

        async def _drive() -> None:
            with (
                patch.object(TrackGenerator, "generate", _registering_generate(calls)),
                patch.object(PoolFiller, "_backoff", AsyncMock()),
            ):
                filler = PoolFiller(TrackGenerator(store))
                filler.ensure_running(("calm", ""), "jazz")
                result.append(await filler.await_first_track())

        asyncio.run(_drive())

        prefix = TrackGenerator.pool_prefix(("calm", "jazz"))
        assert result[0] == store.path_for(f"{prefix}00")

    def test_await_first_track_raises_when_generation_fails(self) -> None:
        store = FakeTrackStore()

        async def always_fail(
            gen: TrackGenerator, vibe: tuple[str, str], style: str, name: str
        ) -> tuple[object, str]:
            msg = "provider down"
            raise RuntimeError(msg)

        async def _drive() -> None:
            with (
                patch.object(TrackGenerator, "generate", always_fail),
                patch.object(PoolFiller, "_backoff", AsyncMock()),
            ):
                filler = PoolFiller(TrackGenerator(store))
                filler.ensure_running(("calm", ""), "jazz")
                with pytest.raises(RuntimeError, match="could not generate the first"):
                    await filler.await_first_track()

        asyncio.run(_drive())


class TestGenerationFailureBackoff:
    """A transient failure backs off and the fill still completes."""

    def test_recovers_after_transient_failure(self) -> None:
        store = FakeTrackStore()
        _seed(store, "calm", "jazz", POOL_SIZE - 1)  # one short of full
        attempts = 0
        filler: PoolFiller | None = None

        async def fail_once(
            gen: TrackGenerator, vibe: tuple[str, str], style: str, name: str
        ) -> tuple[object, str]:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                msg = "transient"
                raise RuntimeError(msg)
            prefix = TrackGenerator.pool_prefix((vibe[0], style))
            n = len(store.tracks_for(prefix))
            stem = f"{prefix}{n:02d}"
            return store.add(stem), stem

        async def _drive() -> None:
            nonlocal filler
            with (
                patch.object(TrackGenerator, "generate", fail_once),
                patch.object(PoolFiller, "_backoff", AsyncMock()),
            ):
                filler = PoolFiller(TrackGenerator(store))
                filler.ensure_running(("calm", ""), "jazz")
                await _drain(filler)

        asyncio.run(_drive())

        assert attempts == 2  # one failure, one success
        assert len(store.tracks_for(TrackGenerator.pool_prefix(("calm", "jazz")))) == (
            POOL_SIZE
        )
