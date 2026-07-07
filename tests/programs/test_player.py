"""Tests for the Player seam protocols."""

from __future__ import annotations

from punt_vox.voxd.programs.player import Player, PlayerProcess


def test_protocols_are_importable() -> None:
    # The seam is a pair of runtime-checkable-free Protocols; importing exercises
    # their definitions and documents the loop's dependency contract.
    assert Player is not None
    assert PlayerProcess is not None
