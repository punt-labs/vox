"""Tests for :mod:`punt_vox.voxd.conversation_mode.input_device`.

The value class alone: the stream it starts or refuses is exercised through
the backend in ``test_portaudio.py``.
"""

from __future__ import annotations

import math

import pytest

from punt_vox.voxd.conversation_mode.input_device import InputDevice


def test_an_empty_device_name_is_refused() -> None:
    with pytest.raises(ValueError, match="name must not be empty"):
        InputDevice("", 48000.0)


@pytest.mark.parametrize("rate", [0.0, -44100.0, math.nan])
def test_a_non_positive_sample_rate_is_refused(rate: float) -> None:
    with pytest.raises(ValueError, match="sample rate must be positive"):
        InputDevice("Built-in", rate)


def test_equal_devices_hash_alike_and_repr_as_their_constructor() -> None:
    device = InputDevice("Built-in", 44100.0)
    assert hash(device) == hash(InputDevice("Built-in", 44100.0))
    assert device != InputDevice("Built-in", 48000.0)
    assert device.__eq__("Built-in") is NotImplemented
    assert repr(device) == "InputDevice('Built-in', 44100.0)"


def test_a_device_knows_the_index_portaudio_gave_it() -> None:
    """The index binds a stream to this device whatever the default becomes."""
    device = InputDevice("Built-in", 44100.0, index=3)
    assert device.index == 3
    # The same device, renumbered by a table re-read, is still the same device.
    assert device == InputDevice("Built-in", 44100.0)
    assert hash(device) == hash(InputDevice("Built-in", 44100.0, index=7))
    assert repr(device) == "InputDevice('Built-in', 44100.0, index=3)"


def test_a_device_described_by_name_alone_has_no_index() -> None:
    assert InputDevice("Built-in", 44100.0).index is None
