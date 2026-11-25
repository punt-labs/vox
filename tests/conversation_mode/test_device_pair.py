"""Tests for :mod:`punt_vox.voxd.conversation_mode.device_pair`."""

from __future__ import annotations

import pytest

from punt_vox.voxd.conversation_mode.device_pair import DevicePair
from punt_vox.voxd.conversation_mode.input_device import InputDevice
from punt_vox.voxd.conversation_mode.output_device import OutputDevice


def _pair(input_name: str, output_name: str) -> DevicePair:
    return DevicePair(
        InputDevice(input_name, 48_000.0), OutputDevice(output_name, 48_000.0)
    )


@pytest.mark.parametrize(
    ("input_name", "output_name"),
    [
        ("J's AirPods #1", "J's AirPods #1"),
        ("MacBook Air Microphone", "MacBook Air Speakers"),
        ("MacBook Pro Microphone", "MacBook Pro Speakers"),
        ("USB Audio Mic", "USB Audio Output"),
        ("Studio Display Microphone", "Studio Display Speakers"),
    ],
)
def test_one_piece_of_hardware_is_shared(input_name: str, output_name: str) -> None:
    assert _pair(input_name, output_name).shared_hardware


@pytest.mark.parametrize(
    ("input_name", "output_name"),
    [
        ("MacBook Air Microphone", "J's AirPods #1"),
        ("J's AirPods #1", "MacBook Air Speakers"),
        ("Blue Yeti", "MacBook Air Speakers"),
        ("MacBook Air Microphone", "External Headphones"),
    ],
)
def test_two_pieces_of_hardware_are_not_shared(
    input_name: str, output_name: str
) -> None:
    assert not _pair(input_name, output_name).shared_hardware


def test_a_name_that_is_only_role_words_compares_as_empty() -> None:
    assert _pair("Microphone", "Speakers").shared_hardware


def test_pairs_compare_by_both_devices() -> None:
    pair = _pair("A", "B")
    assert pair == _pair("A", "B")
    assert pair != _pair("A", "C")
    assert pair != "A"
    assert hash(pair) == hash(_pair("A", "B"))
    assert repr(pair) == (
        "DevicePair(InputDevice('A', 48000.0), OutputDevice('B', 48000.0))"
    )
    assert pair.input == InputDevice("A", 48_000.0)
    assert pair.output == OutputDevice("B", 48_000.0)
