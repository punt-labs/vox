"""The Z ``Key Properties`` asserted by name against the domain.

Each test maps to a named property of ``docs/audio-programs.tex`` section 9 and
the design test plan. They drive reachable states through the transitions and
assert the modelled property holds.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable

import pytest

from punt_vox.voxd.programs import (
    Format,
    Mode,
    Part,
    PlaybackPolicy,
    PlaybackStatus,
    Program,
    ProgramState,
    Reason,
)

PartFactory = Callable[[int], Part]
PoolFactory = Callable[..., frozenset[Part]]

_TRANSITIONS = (
    "turn_on",
    "first_track_ok",
    "first_track_bad_prompt",
    "first_track_transient",
    "fill_ok",
    "fill_bad_part",
    "fill_transient",
    "retry_fails",
    "retry_exhausted",
    "recover",
    "vibe_style_change",
    "turn_off",
    "rotate",
    "play_part",
    "start_from_disk",
)


def _filled(policy: PlaybackPolicy, mk: PartFactory, count: int) -> Program:
    prog = Program(ProgramState.initial(), policy)
    prog.turn_on()
    prog.first_track_ok(mk(1))
    for i in range(2, count + 1):
        prog.fill_ok(mk(i))
    return prog


def test_pool_never_exceeds_size(policy: PlaybackPolicy, mk: PartFactory) -> None:
    prog = _filled(policy, mk, Format.PLAYLIST.pool_size)
    assert len(prog.pool) == Format.PLAYLIST.pool_size
    # A full pool has no active fill, so no further Part can be admitted.
    with pytest.raises(ValueError, match="active fill"):
        prog.fill_ok(mk(99))
    assert len(prog.pool) <= Format.PLAYLIST.pool_size


def test_generation_only_below_full(policy: PlaybackPolicy, mk: PartFactory) -> None:
    partial = _filled(policy, mk, 3)
    assert partial.state.filling is True
    full = _filled(policy, mk, Format.PLAYLIST.pool_size)
    assert full.mode is Mode.PLAYING_ROTATING
    assert full.state.filling is False


def test_playing_in_pool(rotating: Program) -> None:
    rotating.rotate()
    playing = rotating.playing
    assert playing is not None
    assert playing in rotating.pool


def test_full_pool_never_hard_fails(
    policy: PlaybackPolicy, mk: PartFactory, reason: Reason
) -> None:
    prog = _filled(policy, mk, 3)
    prog.fill_transient(reason)  # retrying with a non-empty pool
    for _ in range(4):
        prog.retry_fails(reason)
    # A non-empty pool cannot exhaust into failed -- the guard forbids it.
    with pytest.raises(ValueError, match="empty pool"):
        prog.retry_exhausted(reason)
    assert prog.mode is not Mode.FAILED


def test_two_failure_surfaces(
    policy: PlaybackPolicy, mk: PartFactory, reason: Reason
) -> None:
    # Per-Part surface: the Program stays healthy, error recorded per Part.
    playing_on = _filled(policy, mk, 2)
    playing_on.fill_bad_part(mk(9), reason)
    assert playing_on.state.last_error is None
    assert mk(9) in playing_on.failed_parts

    # Program surface: nothing can play, program-level error is set.
    dead = Program(ProgramState.initial(), policy)
    dead.turn_on()
    dead.first_track_bad_prompt(mk(1), reason)
    assert dead.state.last_error == reason


def test_replay_generates_nothing(
    policy: PlaybackPolicy, mk: PartFactory, pool_of: PoolFactory
) -> None:
    prog = _filled(policy, mk, 3)
    pool_before = prog.pool
    failed_before = prog.failed_parts
    prog.rotate()
    prog.play_part(mk(2))
    assert prog.pool == pool_before
    assert prog.failed_parts == failed_before

    cold = Program(ProgramState.restored(Format.PLAYLIST, pool_of(1, 2)), policy)
    cold.start_from_disk(mk(1))
    assert cold.state.filling is False  # consume path never arms the fill


def test_off_clears_generation_state(
    policy: PlaybackPolicy, mk: PartFactory, reason: Reason
) -> None:
    prog = Program(ProgramState.initial(), policy)
    prog.turn_on()
    prog.first_track_bad_prompt(mk(1), reason)  # -> failed, error + failed part
    prog.turn_off()
    assert prog.mode is Mode.OFF
    assert prog.playing is None
    assert prog.state.filling is False
    assert prog.state.last_error is None
    assert len(prog.failed_parts) == 0


def test_no_session_gate() -> None:
    # No transition takes a session/owner/who parameter -- state is universal.
    forbidden = {"session", "owner", "who", "who_", "session_id"}
    for name in _TRANSITIONS:
        params = set(inspect.signature(getattr(Program, name)).parameters)
        assert not (params & forbidden), f"{name} must not gate on a session"


def test_rotate_avoids_immediate_repeat(rotating: Program) -> None:
    before = rotating.playing
    rotating.rotate()
    assert rotating.playing != before


def test_failed_is_recoverable_via_vibe_change(
    policy: PlaybackPolicy, mk: PartFactory, pool_of: PoolFactory, reason: Reason
) -> None:
    prog = Program(ProgramState.initial(), policy)
    prog.turn_on()
    prog.first_track_bad_prompt(mk(1), reason)
    assert prog.status is PlaybackStatus.FAILED
    prog.vibe_style_change(pool_of(5, 6))  # unconditional recovery, no claim
    assert prog.mode is Mode.PLAYING_FILLING
