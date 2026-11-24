"""Tests for :mod:`punt_vox.voxd.conversation_mode.input_device`.

No real audio hardware: a fake ``sounddevice`` module is installed in
``sys.modules`` so the on-demand imports inside :class:`PortAudio` resolve
to the test's doubles.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from conversation_mode._mic_fakes import FakeInputStream
from punt_vox.voxd.conversation_mode.input_device import InputDevice, PortAudio

if TYPE_CHECKING:
    from collections.abc import Callable

    from sounddevice import DeviceInfo


class _FakePortAudioError(Exception):
    pass


def _device_info(name: str, default_samplerate: float) -> Callable[[str], DeviceInfo]:
    def query_devices(kind: str) -> DeviceInfo:
        assert kind == "input"
        return {"name": name, "default_samplerate": default_samplerate}

    return query_devices


def _install(monkeypatch: pytest.MonkeyPatch, **members: object) -> None:
    fake = SimpleNamespace(PortAudioError=_FakePortAudioError, **members)
    monkeypatch.setitem(sys.modules, "sounddevice", fake)


def test_the_default_device_is_reported_by_name_and_sample_rate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, query_devices=_device_info("MacBook Air Microphone", 48000.0))
    assert PortAudio().device() == InputDevice("MacBook Air Microphone", 48000.0)


def test_an_integer_sample_rate_is_reported_as_a_float(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def query_devices(kind: str) -> DeviceInfo:
        del kind
        return {"name": "Built-in", "default_samplerate": 44100}

    _install(monkeypatch, query_devices=query_devices)
    device = PortAudio().device()
    assert device.default_samplerate_hz == 44100.0
    assert type(device.default_samplerate_hz) is float


def test_no_default_device_is_an_oserror_carrying_portaudios_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_device(kind: str) -> DeviceInfo:
        raise _FakePortAudioError(f"Error querying device {kind}")

    _install(monkeypatch, query_devices=no_device)
    with pytest.raises(OSError, match="no default input device") as failure:
        PortAudio().device()
    assert "Error querying device input" in str(failure.value)
    assert isinstance(failure.value.__cause__, _FakePortAudioError)


def test_open_requests_a_raw_mono_int16_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, RawInputStream=FakeInputStream)

    def callback(*_args: object) -> None:
        pass

    stream = PortAudio().open(samplerate=16000.0, blocksize=320, callback=callback)
    assert isinstance(stream, FakeInputStream)
    assert stream.kwargs == {
        "samplerate": 16000.0,
        "blocksize": 320,
        "channels": 1,
        "dtype": "int16",
        "callback": callback,
    }


def test_a_stream_portaudio_refuses_is_an_oserror_naming_the_device_and_a_remedy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A busy, zero-channel or invalid device is an OSError, not a code bug."""

    def refuse(**_kwargs: object) -> FakeInputStream:
        raise _FakePortAudioError(
            "Error opening RawInputStream: Invalid number of channels [-9998]"
        )

    _install(
        monkeypatch,
        RawInputStream=refuse,
        query_devices=_device_info("USB Audio", 48000.0),
    )
    with pytest.raises(OSError, match="cannot open input device USB Audio") as failure:
        PortAudio().open(samplerate=16000.0, blocksize=320, callback=lambda *_: None)
    message = str(failure.value)
    assert "Invalid number of channels" in message
    assert "another application" in message
    assert isinstance(failure.value.__cause__, _FakePortAudioError)
