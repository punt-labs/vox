"""Tests for the Format enum and its fixed capacities."""

from __future__ import annotations

import pytest

from punt_vox.voxd.programs import MAX_RETRY, Format


@pytest.mark.parametrize(
    ("fmt", "size"),
    [(Format.PLAYLIST, 12), (Format.PODCAST, 6), (Format.AUDIOBOOK, 6)],
)
def test_pool_size(fmt: Format, size: int) -> None:
    assert fmt.pool_size == size


@pytest.mark.parametrize(
    ("fmt", "label"),
    [
        (Format.PLAYLIST, "music"),
        (Format.PODCAST, "podcast"),
        (Format.AUDIOBOOK, "audiobook"),
    ],
)
def test_label(fmt: Format, label: str) -> None:
    assert fmt.label == label


def test_wire_value_is_the_string() -> None:
    assert Format.PLAYLIST.value == "playlist"
    assert Format("playlist") is Format.PLAYLIST


def test_max_retry() -> None:
    assert MAX_RETRY == 5
