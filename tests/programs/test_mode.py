"""Tests for the Mode enum and its coarse PlaybackStatus projection."""

from __future__ import annotations

import pytest

from punt_vox.types_programs import Mode, PlaybackStatus


@pytest.mark.parametrize(
    ("mode", "status"),
    [
        (Mode.OFF, PlaybackStatus.OFF),
        (Mode.GENERATING_FIRST, PlaybackStatus.GENERATING),
        (Mode.PLAYING_FILLING, PlaybackStatus.PLAYING),
        (Mode.PLAYING_ROTATING, PlaybackStatus.PLAYING),
        (Mode.RETRYING, PlaybackStatus.RETRYING),
        (Mode.FAILED, PlaybackStatus.FAILED),
    ],
)
def test_status_projection(mode: Mode, status: PlaybackStatus) -> None:
    assert mode.status is status


def test_every_mode_has_a_status() -> None:
    assert all(isinstance(mode.status, PlaybackStatus) for mode in Mode)


def test_wire_value_is_the_string() -> None:
    assert Mode.PLAYING_FILLING.value == "playing_filling"
