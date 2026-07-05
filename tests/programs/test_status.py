"""Tests for the format-spanning ``ProgramStatus`` observability surface.

Covers the three "not playing / playing" situations that decision O1 turns on
(idle vs off-with-saved-pool vs playing), that both failure surfaces (finding #5)
reach a client distinctly, and that the value round-trips through the wire so a
client reads the daemon's authoritative state rather than a log.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from punt_vox.voxd.programs import (
    Format,
    Mode,
    Part,
    Program,
    ProgramName,
    ProgramState,
    ProgramStatus,
    Reason,
)
from punt_vox.voxd.programs.wire import JsonObject

from .conftest import AvoidRepeatPolicy, build_rotating, make_manifest


def _restored(name: str, *indices: int) -> Program:
    """Return an idle Program over a saved pool (mode off, nothing playing)."""
    manifest = make_manifest(name, *indices)
    state = ProgramState.restored(Format.PLAYLIST, frozenset(manifest.ready_parts()))
    return Program(state, AvoidRepeatPolicy())


def test_idle_has_no_name() -> None:
    """An idle daemon reports name=None and off, distinct from a saved pool."""
    status = ProgramStatus.idle()

    assert status.name is None
    assert status.is_idle
    assert status.mode is Mode.OFF
    assert status.now_playing is None
    assert status.failed_parts == ()


def test_off_with_saved_pool_names_the_program() -> None:
    """An off Program with a disk pool reports name set, off, nothing playing (O1)."""
    status = ProgramStatus.of(_restored("ambient_techno", 1, 2, 3), ProgramName("x"))

    assert status.name == ProgramName("x")
    assert not status.is_idle
    assert status.mode is Mode.OFF
    assert status.now_playing is None  # a pool to play, but not playing


def test_playing_reports_part_n_of_m() -> None:
    """A rotating Program reports the playing Part's 1-based position and pool size."""
    program = build_rotating(AvoidRepeatPolicy())

    status = ProgramStatus.of(program, ProgramName("ambient_techno"))

    assert status.mode is Mode.PLAYING_ROTATING
    assert status.now_playing is not None
    assert status.now_playing.of == Format.PLAYLIST.pool_size
    assert 1 <= status.now_playing.index <= status.now_playing.of


def test_both_failure_surfaces_present_and_distinct() -> None:
    """A per-Part failure surfaces in failed_parts while the program plays on."""
    program = Program(ProgramState.initial(), AvoidRepeatPolicy())
    program.turn_on()
    program.first_track_ok(Part("id001", 1))
    program.fill_bad_part(Part("id002", 2), Reason("bad_prompt: unsafe"))

    status = ProgramStatus.of(program, ProgramName("ambient_techno"))

    # Per-Part surface carries the failure; program-level error stays clear.
    assert len(status.failed_parts) == 1
    assert status.failed_parts[0].index == 2
    assert "bad_prompt" in status.failed_parts[0].reason
    assert status.generation.last_error is None
    assert status.now_playing is not None  # still playing


def test_program_level_failure_sets_last_error() -> None:
    """A first-track permanent failure surfaces as a program-level error."""
    program = Program(ProgramState.initial(), AvoidRepeatPolicy())
    program.turn_on()
    program.first_track_bad_prompt(Part("id001", 1), Reason("bad_prompt: nope"))

    status = ProgramStatus.of(program, ProgramName("ambient_techno"))

    assert status.mode is Mode.FAILED
    assert status.generation.last_error is not None
    assert "bad_prompt" in status.generation.last_error
    assert status.now_playing is None


@pytest.mark.parametrize(
    "build",
    [
        ProgramStatus.idle,
        lambda: ProgramStatus.of(_restored("saved", 1, 2), ProgramName("saved")),
        lambda: ProgramStatus.of(build_rotating(AvoidRepeatPolicy()), ProgramName("r")),
    ],
)
def test_wire_round_trips(build: Callable[[], ProgramStatus]) -> None:
    """Every status shape survives a JSON round-trip unchanged (client-observable)."""
    original = build()

    restored = ProgramStatus.from_wire(JsonObject.coerce(original.to_dict(), "status"))

    assert restored == original


def test_wire_round_trips_failed_parts() -> None:
    """A failed-Part surface survives the wire so a client sees it, not a log."""
    program = Program(ProgramState.initial(), AvoidRepeatPolicy())
    program.turn_on()
    program.first_track_ok(Part("id001", 1))
    program.fill_bad_part(Part("id002", 2), Reason("ToS violation"))
    original = ProgramStatus.of(program, ProgramName("ambient_techno"))

    restored = ProgramStatus.from_wire(JsonObject.coerce(original.to_dict(), "status"))

    assert restored == original
    assert restored.failed_parts[0].reason == "ToS violation"
