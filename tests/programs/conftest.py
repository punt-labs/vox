"""Shared fixtures and in-memory playback-policy fakes for the domain tests.

The domain is pure, so the only injected seam Phase-1 tests need is the
``PlaybackPolicy``. Three fakes cover the branches ``Program.rotate`` cares
about: one that avoids an immediate repeat (stands in for the real
``RotatePolicy``), one that always returns a named Part, and one that signals
``COMPLETE`` (to exercise the "a playlist has no end" assertion arm).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import final

import pytest

from punt_vox.voxd.programs import (
    COMPLETE,
    Advance,
    AdvanceResult,
    Format,
    Part,
    Program,
    ProgramState,
    Reason,
)


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
def rotating() -> Program:
    """Return a Program driven to ``playing_rotating`` with a full 12-Part pool."""
    prog = Program(ProgramState.initial(), AvoidRepeatPolicy())
    prog.turn_on()
    prog.first_track_ok(make_part(1))
    for i in range(2, Format.PLAYLIST.pool_size + 1):
        prog.fill_ok(make_part(i))
    return prog
