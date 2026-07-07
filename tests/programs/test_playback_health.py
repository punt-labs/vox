"""Tests for :class:`PlaybackHealth` and :class:`PlaybackFault`.

The health slot records a player spawn failure so ``status`` can surface it, and
clears it on recovery. The fault value round-trips through the wire so a client
reads a standing playback problem, never only a daemon log (vox-ig52).
"""

from __future__ import annotations

from punt_vox.voxd.programs.part import Part
from punt_vox.voxd.programs.playback_health import PlaybackFault, PlaybackHealth
from punt_vox.voxd.programs.wire import JsonObject


def test_starts_healthy() -> None:
    """A fresh health slot reports no fault."""
    assert PlaybackHealth().fault is None


def test_records_a_spawn_failure() -> None:
    """Recording a failure exposes the Part's index and the reason via status."""
    health = PlaybackHealth()

    health.record(Part("003.mp3", 3), "afplay: No such file or directory")

    fault = health.fault
    assert fault is not None
    assert fault.part_index == 3
    assert "No such file" in fault.reason


def test_clear_restores_health() -> None:
    """A successful spawn clears the standing fault."""
    health = PlaybackHealth()
    health.record(Part("001.mp3", 1), "boom")

    health.clear()

    assert health.fault is None


def test_latest_failure_replaces_the_prior() -> None:
    """Only the standing fault is kept -- a new failure supersedes the old."""
    health = PlaybackHealth()
    health.record(Part("001.mp3", 1), "first")
    health.record(Part("002.mp3", 2), "second")

    fault = health.fault
    assert fault is not None
    assert fault.part_index == 2
    assert fault.reason == "second"


def test_fault_round_trips_through_wire() -> None:
    """The fault survives a JSON round-trip so a client reads it, not a log."""
    original = PlaybackFault(part_index=4, reason="EMFILE: too many open files")

    restored = PlaybackFault.from_wire(JsonObject.coerce(original.to_dict(), "fault"))

    assert restored == original
