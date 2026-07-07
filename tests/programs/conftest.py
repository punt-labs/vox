"""Shared fixtures and in-memory playback-policy fakes for the domain tests.

The domain is pure, so the only injected seam Phase-1 tests need is the
``PlaybackPolicy``. Three fakes cover the branches ``Program.rotate`` cares
about: one that avoids an immediate repeat (stands in for the real
``RotatePolicy``), one that always returns a named Part, and one that signals
``COMPLETE`` (to exercise the "a playlist has no end" assertion arm).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Self, final

import pytest

from punt_vox.voxd.programs import (
    COMPLETE,
    Advance,
    AdvanceResult,
    Format,
    Part,
    PlaybackPolicy,
    Program,
    ProgramState,
    Reason,
)
from punt_vox.voxd.programs.filesystem_store import FilesystemProgramStore
from punt_vox.voxd.programs.identifiers import ProgramName
from punt_vox.voxd.programs.manifest import (
    PartEntry,
    PlaylistSubject,
    ProgramManifest,
)
from punt_vox.voxd.programs.part import PartStatus
from punt_vox.voxd.programs.producer import PartSpec
from punt_vox.voxd.programs.service import ProgramService


@final
class AvoidRepeatPolicy:
    """Return the first pool Part that is not currently playing (anti-repeat)."""

    def next_part(self, pool: tuple[Part, ...], playing: Part | None) -> AdvanceResult:
        for part in pool:
            if part != playing:
                return Advance(part)
        return Advance(pool[0])


@final
@dataclass(frozen=True, slots=True)
class FixedPolicy:
    """Always return the same Part -- lets a test pin ``rotate``'s successor."""

    target: Part

    def next_part(self, pool: tuple[Part, ...], playing: Part | None) -> AdvanceResult:
        return Advance(self.target)


@final
class CompletePolicy:
    """Signal end-of-list -- unreachable for a playlist, so ``rotate`` asserts."""

    def next_part(self, pool: tuple[Part, ...], playing: Part | None) -> AdvanceResult:
        return COMPLETE


@final
class FakeSleeper:
    """A no-op Sleeper -- backoff is instant so retry paths run in microseconds."""

    __slots__ = ("sleeps",)
    sleeps: list[float]

    def __new__(cls) -> Self:
        self = super().__new__(cls)
        self.sleeps = []
        return self

    async def sleep(self, seconds: float) -> None:
        # Yield to the event loop (like a real sleep) so a backoff never starves
        # a co-running task, then return instantly -- tests never really wait.
        await asyncio.sleep(0)
        self.sleeps.append(seconds)


def make_part(index: int) -> Part:
    """Build a Part whose identity is derived from its 1-based index."""
    return Part(f"id{index:03d}", index)


def make_pool(*indices: int) -> frozenset[Part]:
    """Build a pool of Parts from a series of 1-based indices."""
    return frozenset(make_part(i) for i in indices)


@pytest.fixture
def mk() -> Callable[[int], Part]:
    """Return the Part factory."""
    return make_part


@pytest.fixture
def pool_of() -> Callable[..., frozenset[Part]]:
    """Return the pool factory."""
    return make_pool


@pytest.fixture
def policy() -> AvoidRepeatPolicy:
    """Return the default anti-repeat playback policy."""
    return AvoidRepeatPolicy()


@pytest.fixture
def reason() -> Reason:
    """Return a reusable diagnostic reason."""
    return Reason("boom")


@pytest.fixture
def sleeper() -> FakeSleeper:
    """Return a no-op Sleeper so backoff paths run instantly."""
    return FakeSleeper()


def build_rotating(policy: PlaybackPolicy) -> Program:
    """Drive a fresh Program to ``playing_rotating`` with a full 12-Part pool."""
    prog = Program(ProgramState.initial(), policy)
    prog.turn_on()
    prog.first_track_ok(make_part(1))
    for i in range(2, Format.PLAYLIST.pool_size + 1):
        prog.fill_ok(make_part(i))
    return prog


@pytest.fixture
def rotating() -> Program:
    """Return a Program driven to ``playing_rotating`` with a full 12-Part pool."""
    return build_rotating(AvoidRepeatPolicy())


@pytest.fixture
def make_rotating() -> Callable[[PlaybackPolicy], Program]:
    """Return the factory that builds a fresh full-pool rotating Program."""
    return build_rotating


# ---------------------------------------------------------------------------
# In-memory persistence fakes -- structural PartStore/ProgramStore doubles.
# They generalize today's FakeTrackStore: dict-backed, filesystem-free, so the
# domain and (slice-3) loop tests never touch the disk. Parity with the
# filesystem impls is asserted in test_store.py.
# ---------------------------------------------------------------------------


def ready_entry(index: int, duration_ms: int = 120_000) -> PartEntry:
    """Build a ready manifest entry addressing ``NNN.mp3`` at ``index``."""
    return PartEntry(
        index=index,
        file=f"{index:03d}.mp3",
        status=PartStatus.READY,
        duration_ms=duration_ms,
    )


def make_manifest(name: str = "ambient_techno", *indices: int) -> ProgramManifest:
    """Build a playlist manifest named ``name`` with ready Parts at ``indices``."""
    return ProgramManifest(
        name=ProgramName(name),
        fmt=Format.PLAYLIST,
        subject=PlaylistSubject(vibe="ambient", style="techno"),
        parts=tuple(ready_entry(i) for i in indices),
    )


@final
class InMemoryPartStore:
    """A dict-backed PartStore: holds one Program's manifest in memory."""

    __slots__ = ("_manifest",)
    _manifest: ProgramManifest

    def __new__(cls, manifest: ProgramManifest) -> Self:
        self = super().__new__(cls)
        self._manifest = manifest
        return self

    def ready_parts(self) -> tuple[Part, ...]:
        return self._manifest.ready_parts()

    def next_index(self) -> int:
        return self._manifest.next_index()

    def write_target(self, index: int) -> Path:
        # Synthetic path -- a faked Producer never writes it.
        return Path(f"{index:03d}.mp3")

    def record(self, entry: PartEntry) -> None:
        self._manifest = self._manifest.with_part(entry)

    def manifest(self) -> ProgramManifest:
        return self._manifest

    def prepare(self) -> None:
        return None


@final
class InMemoryProgramStore:
    """A dict-backed ProgramStore keyed by Program name."""

    __slots__ = ("_stores",)
    _stores: dict[str, InMemoryPartStore]

    def __new__(cls) -> Self:
        self = super().__new__(cls)
        self._stores = {}
        return self

    def list_programs(self) -> tuple[ProgramManifest, ...]:
        return tuple(
            sorted(
                (store.manifest() for store in self._stores.values()),
                key=lambda m: m.name.value,
            )
        )

    def resolve(self, name: ProgramName) -> ProgramManifest | None:
        store = self._stores.get(name.value)
        return None if store is None else store.manifest()

    def open(self, name: ProgramName) -> InMemoryPartStore:
        store = self._stores.get(name.value)
        if store is None:
            msg = f"no saved program named {name.value!r}"
            raise LookupError(msg)
        return store

    def create(self, manifest: ProgramManifest) -> InMemoryPartStore:
        store = InMemoryPartStore(manifest)
        self._stores[manifest.name.value] = store
        return store


@pytest.fixture
def program_store() -> InMemoryProgramStore:
    """Return an empty in-memory program store."""
    return InMemoryProgramStore()


@pytest.fixture
def manifest_of() -> Callable[..., ProgramManifest]:
    """Return the playlist-manifest factory."""
    return make_manifest


@pytest.fixture
def entry_of() -> Callable[..., PartEntry]:
    """Return the ready-entry factory."""
    return ready_entry


# ---------------------------------------------------------------------------
# Daemon-orchestration doubles -- a filesystem-backed service for handler/service
# tests. The Producer writes a byte so ``FilesystemPartStore`` records a real
# Part; the store is real (under ``tmp_path``) so replay resolves from disk.
# ---------------------------------------------------------------------------


@final
class QuietProducer:
    """Write a byte to the target and return a ready Part (records each call)."""

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


def make_service(root: Path) -> ProgramService:
    """Build a ProgramService over a real filesystem store rooted at ``root``."""
    return ProgramService(
        QuietProducer(), FilesystemProgramStore(root), root, FakeSleeper()
    )


def seed_program(root: Path, name: str, *indices: int) -> None:
    """Create a saved Program on disk with ready Parts at ``indices``."""
    store = FilesystemProgramStore(root).create(
        ProgramManifest(
            name=ProgramName(name),
            fmt=Format.PLAYLIST,
            subject=PlaylistSubject(vibe="ambient", style="techno"),
            parts=tuple(ready_entry(i) for i in indices),
        )
    )
    for i in indices:
        store.write_target(i).write_bytes(b"audio")
