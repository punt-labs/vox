"""Tests for :mod:`punt_vox.voxd.conversation_mode.device_pair`."""

from __future__ import annotations

import logging

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


@pytest.mark.parametrize(
    ("input_name", "output_name"),
    [
        ("Microphone", "Speakers"),
        ("Mic", "J's AirPods #1"),
        ("MacBook Air Microphone", "Headphones"),
    ],
)
def test_names_that_are_only_role_words_are_not_known_to_be_shared(
    input_name: str, output_name: str
) -> None:
    """Two generic names both reduce to nothing; nothing equals nothing.

    "Microphone" and "Speakers" are two unrelated devices as far as their
    names say, so the pair is neither claimed as shared nor as distinct.
    """
    pair = _pair(input_name, output_name)
    assert not pair.named_hardware
    assert not pair.shared_hardware


def test_named_hardware_is_compared() -> None:
    assert _pair("MacBook Air Microphone", "MacBook Air Speakers").named_hardware
    assert _pair("Blue Yeti", "MacBook Air Speakers").named_hardware


def test_log_says_when_the_names_cannot_be_told_apart(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO):
        _pair("Microphone", "Speakers").log(sample_rate_hz=16_000)
    assert not [r for r in caplog.records if "echo configuration" in r.message]
    assert not [r for r in caplog.records if "same hardware" in r.message]
    (record,) = [r for r in caplog.records if "audio hardware" in r.message]
    assert record.levelno == logging.INFO
    assert record.message == (
        "Call audio hardware: input Microphone and output Speakers cannot be told "
        "apart by name, so whether they share hardware is unknown"
    )


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
