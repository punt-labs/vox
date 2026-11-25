"""Tests for :mod:`punt_vox.voxd.conversation_mode.probe_backend`."""

from __future__ import annotations

from punt_vox.voxd.conversation_mode.device_table import DeviceTable
from punt_vox.voxd.conversation_mode.portaudio import PortAudio
from punt_vox.voxd.conversation_mode.probe_backend import ProbeBackend


def test_portaudio_is_a_probe_backend() -> None:
    """The daemon's real backend satisfies the one method a probe needs."""
    assert isinstance(PortAudio(DeviceTable()), ProbeBackend)


def test_an_object_without_the_method_is_not_a_probe_backend() -> None:
    assert not isinstance(object(), ProbeBackend)
