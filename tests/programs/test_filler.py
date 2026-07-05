"""Behavioral-parity snapshot for the background fill (ports test_filler.py).

Every test drives the REAL fill task against a filesystem PartStore and a fake
Producer. The scenarios mirror the old PoolFiller suite -- fill-to-full, partial
resume, full-pool-no-task, cancel-freezes-count-no-orphan, retarget-cancels-old,
single-in-flight-across-retarget-discards-orphan, transient-backoff-recovers --
plus the new permanent-failure-per-part path. The single-flight
``asyncio.shield`` / orphan-discard machinery is proven byte-for-byte equivalent
before slice 5 deletes the original.
"""
# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import Self, final

from punt_vox.voxd.programs import (
    Format,
    Part,
    PartStatus,
    PlaybackPolicy,
    Program,
    ProgramName,
    ProgramState,
)
from punt_vox.voxd.programs.control_channel import ControlChannel
from punt_vox.voxd.programs.filesystem_store import FilesystemProgramStore
from punt_vox.voxd.programs.filler import Filler, FillPlan
from punt_vox.voxd.programs.manifest import (
    PartEntry,
    PlaylistSubject,
    ProgramManifest,
)
from punt_vox.voxd.programs.producer import (
    PartSpec,
    ProducerBadInputError,
    ProducerTransientError,
)
from punt_vox.voxd.programs.sleeper import Sleeper
from punt_vox.voxd.programs.store import PartStore

_FULL = Format.PLAYLIST.pool_size


@final
class WritingProducer:
    """Write a byte to the target and record each produced index."""

    __slots__ = ("calls",)
    calls: list[int]

    def __new__(cls) -> Self:
        self = super().__new__(cls)
        self.calls = []
        return self

    async def produce(self, spec: PartSpec, target: Path) -> Part:
        self.calls.append(spec.index)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"audio")
        return Part(target.name, spec.index)


def _seeded_store(tmp_path: Path, count: int, name: str = "prog") -> PartStore:
    """Build a filesystem PartStore seeded with ``count`` ready Parts."""
    manifest = ProgramManifest(
        name=ProgramName(name),
        fmt=Format.PLAYLIST,
        subject=PlaylistSubject(vibe="calm", style="jazz"),
        parts=tuple(
            PartEntry(index=i, file=f"{i:03d}.mp3", status=PartStatus.READY)
            for i in range(1, count + 1)
        ),
    )
    return FilesystemProgramStore(tmp_path).create(manifest)


def _plan(store: PartStore, vibe: str = "calm", style: str = "jazz") -> FillPlan:
    return FillPlan(store, PlaylistSubject(vibe=vibe, style=style), ("p1", "p2", "p3"))


def _channel(policy: PlaybackPolicy) -> ControlChannel:
    return ControlChannel(Program(ProgramState.initial(), policy))


async def _drain(filler: Filler) -> None:
    task = filler._task  # test drives the real fill task
    assert task is not None
    await task


class TestFillToPoolSize:
    async def test_fills_to_full_then_stops(
        self, tmp_path: Path, policy: PlaybackPolicy, sleeper: Sleeper
    ) -> None:
        store = _seeded_store(tmp_path, 3)
        producer = WritingProducer()
        filler = Filler(producer, _channel(policy), sleeper)
        filler.ensure_running(_plan(store))
        await _drain(filler)
        assert len(store.ready_parts()) == _FULL
        assert len(producer.calls) == _FULL - 3  # only the shortfall
        assert filler._task is not None and filler._task.done()
        assert not filler._task.cancelled()

    async def test_partial_pool_resumes_from_disk_count(
        self, tmp_path: Path, policy: PlaybackPolicy, sleeper: Sleeper
    ) -> None:
        store = _seeded_store(tmp_path, 5)
        producer = WritingProducer()
        filler = Filler(producer, _channel(policy), sleeper)
        filler.ensure_running(_plan(store))
        await _drain(filler)
        assert len(producer.calls) == _FULL - 5

    async def test_full_pool_starts_no_task(
        self, tmp_path: Path, policy: PlaybackPolicy, sleeper: Sleeper
    ) -> None:
        store = _seeded_store(tmp_path, _FULL)
        producer = WritingProducer()
        filler = Filler(producer, _channel(policy), sleeper)
        filler.ensure_running(_plan(store))
        await asyncio.sleep(0)
        assert not filler.is_running
        assert producer.calls == []

    async def test_same_plan_is_idempotent(
        self, tmp_path: Path, policy: PlaybackPolicy, sleeper: Sleeper
    ) -> None:
        store = _seeded_store(tmp_path, 0)
        hold = asyncio.Event()

        @final
        class HoldingProducer:
            async def produce(self, spec: PartSpec, target: Path) -> Part:
                await hold.wait()  # keep the fill task live
                target.write_bytes(b"x")
                return Part(target.name, spec.index)

        filler = Filler(HoldingProducer(), _channel(policy), sleeper)
        plan = _plan(store)
        filler.ensure_running(plan)
        await asyncio.sleep(0)
        task = filler._task
        filler.ensure_running(plan)  # same plan, still running -> no-op
        assert filler._task is task
        filler.cancel()


class TestCancelStopsGeneration:
    async def test_cancel_freezes_count_no_orphan(
        self, tmp_path: Path, policy: PlaybackPolicy, sleeper: Sleeper
    ) -> None:
        store = _seeded_store(tmp_path, 1)
        started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        @final
        class BlockingProducer:
            async def produce(self, spec: PartSpec, target: Path) -> Part:
                nonlocal calls
                calls += 1
                started.set()
                await release.wait()  # never released -> cancelled mid-flight
                target.write_bytes(b"x")
                return Part(target.name, spec.index)

        filler = Filler(BlockingProducer(), _channel(policy), sleeper)
        filler.ensure_running(_plan(store))
        await asyncio.wait_for(started.wait(), timeout=2.0)
        task = filler._task
        filler.cancel()
        await asyncio.sleep(0)
        assert task is not None
        with contextlib.suppress(asyncio.CancelledError):
            await task
        assert task.cancelled()
        assert calls == 1  # the in-flight generation; no Part was added
        assert not filler.is_running
        assert len(store.ready_parts()) == 1


class TestRetargetCancelsOldTask:
    async def test_retarget_switches_store(
        self, tmp_path: Path, policy: PlaybackPolicy, sleeper: Sleeper
    ) -> None:
        store_a = _seeded_store(tmp_path, 0, "a")
        store_b = _seeded_store(tmp_path, 0, "b")
        started_a = asyncio.Event()
        release = asyncio.Event()

        @final
        class GatedProducer:
            async def produce(self, spec: PartSpec, target: Path) -> Part:
                if not started_a.is_set():
                    started_a.set()
                    await release.wait()  # hold plan A's first generation
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"x")
                return Part(target.name, spec.index)

        filler = Filler(GatedProducer(), _channel(policy), sleeper)
        filler.ensure_running(_plan(store_a))
        await asyncio.wait_for(started_a.wait(), timeout=2.0)
        old_task = filler._task
        assert old_task is not None
        filler.ensure_running(_plan(store_b, "bright", "techno"))  # retarget
        await asyncio.sleep(0)
        release.set()  # single-flight: B waits for A's in-flight to drain
        await _drain(filler)
        assert old_task.cancelled()
        assert len(store_b.ready_parts()) == _FULL


class TestSingleInFlightAcrossRetarget:
    async def test_retarget_bounds_concurrency_and_discards_orphan(
        self, tmp_path: Path, policy: PlaybackPolicy, sleeper: Sleeper
    ) -> None:
        store_a = _seeded_store(tmp_path, 0, "a")
        store_b = _seeded_store(tmp_path, 0, "b")
        started = asyncio.Event()
        release_a = asyncio.Event()
        concurrency = 0
        max_concurrency = 0

        async def _core(target: Path) -> None:
            nonlocal concurrency, max_concurrency
            concurrency += 1
            max_concurrency = max(max_concurrency, concurrency)
            if not started.is_set():
                started.set()
                await release_a.wait()  # hold pool A's first generation open
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"x")  # the write completes despite the cancel
            concurrency -= 1

        @final
        class UncancellableProducer:
            async def produce(self, spec: PartSpec, target: Path) -> Part:
                # shield models asyncio.to_thread: the work is not cancellable.
                await asyncio.shield(_core(target))
                return Part(target.name, spec.index)

        orphan = store_a.write_target(1)
        filler = Filler(UncancellableProducer(), _channel(policy), sleeper)
        filler.ensure_running(_plan(store_a))
        await asyncio.wait_for(started.wait(), timeout=2.0)
        filler.ensure_running(_plan(store_b, "bright", "techno"))  # retarget -> B
        await asyncio.sleep(0)
        assert concurrency == 1  # B is blocked; only A is in flight
        release_a.set()
        await _drain(filler)
        for _ in range(5):
            await asyncio.sleep(0)  # let the discard callback settle
        assert max_concurrency == 1  # A and B never ran concurrently
        assert not orphan.exists()  # the orphan was discarded
        assert len(store_b.ready_parts()) == _FULL


class TestFailureHandling:
    async def test_transient_backoff_recovers(
        self, tmp_path: Path, policy: PlaybackPolicy, sleeper: Sleeper
    ) -> None:
        store = _seeded_store(tmp_path, _FULL - 1)  # one short of full
        attempts = 0

        @final
        class FailOnceProducer:
            async def produce(self, spec: PartSpec, target: Path) -> Part:
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise ProducerTransientError("429")
                target.write_bytes(b"x")
                return Part(target.name, spec.index)

        filler = Filler(FailOnceProducer(), _channel(policy), sleeper)
        filler.ensure_running(_plan(store))
        await _drain(filler)
        assert attempts == 2  # one failure, one success
        assert len(store.ready_parts()) == _FULL

    async def test_permanent_failure_records_failed_and_continues(
        self, tmp_path: Path, policy: PlaybackPolicy, sleeper: Sleeper
    ) -> None:
        store = _seeded_store(tmp_path, _FULL - 1)
        seen: list[int] = []

        @final
        class BadFirstProducer:
            async def produce(self, spec: PartSpec, target: Path) -> Part:
                seen.append(spec.index)
                if len(seen) == 1:
                    raise ProducerBadInputError("bad_prompt")
                target.write_bytes(b"x")
                return Part(target.name, spec.index)

        filler = Filler(BadFirstProducer(), _channel(policy), sleeper)
        filler.ensure_running(_plan(store))
        await _drain(filler)
        # The failed Part is recorded (not ready); the fill advances to the next
        # index and completes the ready pool.
        assert len(store.ready_parts()) == _FULL
        failed = [e for e in store.manifest().parts if not e.is_ready]
        assert len(failed) == 1
