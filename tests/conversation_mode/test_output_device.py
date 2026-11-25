"""Tests for :mod:`punt_vox.voxd.conversation_mode.output_device`."""

from __future__ import annotations

import pytest

from punt_vox.voxd.conversation_mode.output_device import OutputDevice


def test_reports_its_name_and_rate() -> None:
    device = OutputDevice("J's AirPods #1", 24_000.0)
    assert device.name == "J's AirPods #1"
    assert device.default_samplerate_hz == 24_000.0


def test_an_empty_name_is_refused() -> None:
    with pytest.raises(ValueError, match="output device name must not be empty"):
        OutputDevice("", 48_000.0)


@pytest.mark.parametrize("rate", [0.0, -1.0, float("nan")])
def test_a_non_positive_rate_is_refused(rate: float) -> None:
    with pytest.raises(ValueError, match="sample rate must be positive"):
        OutputDevice("Speakers", rate)


def test_devices_compare_by_name_and_rate() -> None:
    device = OutputDevice("Speakers", 48_000.0)
    assert device == OutputDevice("Speakers", 48_000.0)
    assert device != OutputDevice("Headset", 48_000.0)
    assert device != OutputDevice("Speakers", 44_100.0)
    assert device != "Speakers"
    assert hash(device) == hash(OutputDevice("Speakers", 48_000.0))
    assert repr(device) == "OutputDevice('Speakers', 48000.0)"
