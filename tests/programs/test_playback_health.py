"""Tests for :class:`PlaybackHealth` and :class:`PlaybackFault`.

The health slot records a player fault so ``status`` can surface it, and clears it
on recovery. Two fault kinds share the slot -- a spawn failure and a non-zero
track exit -- distinguished by ``kind``. The fault value round-trips through the
wire so a client reads a standing playback problem, never only a daemon log.
"""

from __future__ import annotations

from punt_vox.voxd.programs.part import Part
from punt_vox.voxd.programs.playback_health import (
    PlaybackFault,
    PlaybackFaultKind,
    PlaybackHealth,
)
from punt_vox.voxd.programs.wire import JsonObject


def test_starts_healthy() -> None:
    """A fresh health slot reports no fault."""
    assert PlaybackHealth().fault is None


def test_records_a_spawn_failure() -> None:
    """Recording a spawn failure exposes the Part, reason, and kind via status."""
    health = PlaybackHealth()

    health.record(
        Part("003.mp3", 3), "afplay: No such file or directory", PlaybackFaultKind.SPAWN
    )

    fault = health.fault
    assert fault is not None
    assert fault.part_index == 3
    assert "No such file" in fault.reason
    assert fault.kind is PlaybackFaultKind.SPAWN


def test_records_a_track_exit_fault_distinctly() -> None:
    """A non-zero track exit is recorded with the track_exit kind, not spawn."""
    health = PlaybackHealth()

    health.record(
        Part("004.mp3", 4), "player exited with code 1", PlaybackFaultKind.TRACK_EXIT
    )

    fault = health.fault
    assert fault is not None
    assert fault.kind is PlaybackFaultKind.TRACK_EXIT


def test_clear_restores_health() -> None:
    """A successful spawn clears the standing fault."""
    health = PlaybackHealth()
    health.record(Part("001.mp3", 1), "boom", PlaybackFaultKind.SPAWN)

    health.clear()

    assert health.fault is None


def test_latest_failure_replaces_the_prior() -> None:
    """Only the standing fault is kept -- a new failure supersedes the old."""
    health = PlaybackHealth()
    health.record(Part("001.mp3", 1), "first", PlaybackFaultKind.SPAWN)
    health.record(Part("002.mp3", 2), "second", PlaybackFaultKind.TRACK_EXIT)

    fault = health.fault
    assert fault is not None
    assert fault.part_index == 2
    assert fault.reason == "second"
    assert fault.kind is PlaybackFaultKind.TRACK_EXIT


def test_fault_round_trips_through_wire() -> None:
    """The fault survives a JSON round-trip so a client reads it, not a log."""
    original = PlaybackFault(
        part_index=4,
        reason="EMFILE: too many open files",
        kind=PlaybackFaultKind.SPAWN,
    )

    restored = PlaybackFault.from_wire(JsonObject.coerce(original.to_dict(), "fault"))

    assert restored == original
    assert restored.kind is PlaybackFaultKind.SPAWN
