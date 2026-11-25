"""Tests for :mod:`punt_vox.voxd.conversation_mode.portaudio`.

No real audio hardware: a fake ``sounddevice`` module is installed in
``sys.modules`` so the on-demand imports inside :class:`PortAudio` resolve
to the test's doubles.
"""

from __future__ import annotations

import logging
import sys
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from conversation_mode._fake_sounddevice import FakePortAudioError, FakeSoundDevice
from conversation_mode._mic_fakes import FakeInputStream
from punt_vox.voxd.conversation_mode.device_pair import DevicePair
from punt_vox.voxd.conversation_mode.device_table import DeviceTable
from punt_vox.voxd.conversation_mode.input_device import InputDevice
from punt_vox.voxd.conversation_mode.output_device import OutputDevice
from punt_vox.voxd.conversation_mode.portaudio import PortAudio

if TYPE_CHECKING:
    from collections.abc import Callable

    from sounddevice import DeviceInfo

# The close-after-start-failure warning is logged by the device, not the backend.
_DEVICE_MODULE = "punt_vox.voxd.conversation_mode.input_device"


class _FakePortAudioError(Exception):
    pass


def _device_info(name: str, default_samplerate: float) -> Callable[[str], DeviceInfo]:
    def query_devices(kind: str) -> DeviceInfo:
        assert kind in {"input", "output"}
        return {"name": name, "default_samplerate": default_samplerate}

    return query_devices


USB = InputDevice("USB Audio", 48000.0)


def _ignore(*_args: object) -> None:
    pass


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


@pytest.mark.parametrize(
    ("name", "default_samplerate", "reason"),
    [
        ("USB Audio", 0.0, "sample rate must be positive"),
        ("", 48000.0, "name must not be empty"),
    ],
)
def test_a_device_reported_without_a_name_or_rate_is_an_oserror(
    monkeypatch: pytest.MonkeyPatch, name: str, default_samplerate: float, reason: str
) -> None:
    """A device PortAudio reports but cannot describe is no usable device.

    The launcher releases the call ID on an OSError only; the ValueError the
    device's validation raises would escape it as a bad-script error with the
    ID still reserved.
    """
    _install(monkeypatch, query_devices=_device_info(name, default_samplerate))
    with pytest.raises(OSError, match="no usable default input device") as failure:
        PortAudio().device()
    assert reason in str(failure.value)
    assert isinstance(failure.value.__cause__, ValueError)


def test_open_requests_a_raw_mono_int16_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[FakeInputStream] = []

    def construct(**kwargs: object) -> FakeInputStream:
        stream = FakeInputStream(**kwargs)
        created.append(stream)
        return stream

    _install(
        monkeypatch,
        RawInputStream=construct,
        query_devices=_device_info("USB Audio", 48000.0),
    )

    def callback(*_args: object) -> None:
        pass

    table = DeviceTable()
    stream = PortAudio(table).open(
        USB, samplerate=16000.0, blocksize=320, callback=callback
    )
    assert table.open_streams == 1
    stream.__exit__(None, None, None)
    assert table.open_streams == 0
    stream.__exit__(None, None, None)  # Idempotent, like the stream itself.
    assert table.open_streams == 0
    (opened,) = created
    assert opened.closed
    assert opened.kwargs == {
        "samplerate": 16000.0,
        "blocksize": 320,
        "channels": 1,
        "dtype": "int16",
        "callback": callback,
    }


def test_the_count_is_raised_before_the_native_input_stream_is_created(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invariant: refresh never terminates PortAudio while a stream is reserved.

    A refresh on another thread that lands while PortAudio is creating the
    native stream must find the count already raised.
    """
    fake = FakeSoundDevice(input_name="Built-in", output_name="Built-in").install(
        monkeypatch
    )
    table = DeviceTable()
    refreshed: list[bool] = []

    def create(**kwargs: object) -> FakeInputStream:
        fake.connect("Headset")  # A device change arrives mid-creation.
        refreshed.append(table.refresh())
        return FakeInputStream(**kwargs)

    monkeypatch.setattr(sys.modules["sounddevice"], "RawInputStream", create)
    held = PortAudio(table).open(
        USB, samplerate=16000.0, blocksize=320, callback=_ignore
    )
    assert refreshed == [False]
    assert fake.terminated == 0
    assert table.open_streams == 1
    held.__exit__(None, None, None)
    assert table.open_streams == 0


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

    def callback(*_args: object) -> None:
        pass

    table = DeviceTable()
    with pytest.raises(OSError, match="cannot open input device USB Audio") as failure:
        PortAudio(table).open(USB, samplerate=16000.0, blocksize=320, callback=callback)
    message = str(failure.value)
    assert "Invalid number of channels" in message
    assert "another application" in message
    assert isinstance(failure.value.__cause__, _FakePortAudioError)
    assert table.open_streams == 0  # The reservation was given back.


def test_a_stream_that_cannot_start_is_the_same_oserror_and_is_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """sounddevice opens in the constructor and starts in __enter__.

    A start failure is a PortAudioError, not an OSError; left unconverted it
    would reach the launcher as neither a refused device nor a known RPC
    error, with the device handle still open.
    """
    created: list[FakeInputStream] = []

    def construct(**kwargs: object) -> FakeInputStream:
        error = _FakePortAudioError("Error starting stream: Device unavailable [-9985]")
        stream = FakeInputStream.unstartable(error, **kwargs)
        created.append(stream)
        return stream

    _install(
        monkeypatch,
        RawInputStream=construct,
        query_devices=_device_info("USB Audio", 48000.0),
    )

    def callback(*_args: object) -> None:
        pass

    table = DeviceTable()
    with pytest.raises(OSError, match="cannot open input device USB Audio") as failure:
        PortAudio(table).open(USB, samplerate=16000.0, blocksize=320, callback=callback)
    assert "Device unavailable" in str(failure.value)
    assert isinstance(failure.value.__cause__, _FakePortAudioError)
    (stream,) = created
    assert stream.closed
    assert table.open_streams == 0  # The reservation was given back.


def test_a_stream_that_cannot_start_nor_close_is_still_the_device_oserror(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A close that fails after a start failure must not hide the device error.

    The caller releases the call ID and names the device on an OSError; a
    raw PortAudioError from close would reach it as neither.
    """
    caplog.set_level(logging.WARNING, logger=_DEVICE_MODULE)

    def construct(**kwargs: object) -> FakeInputStream:
        error = _FakePortAudioError("Error starting stream: Device unavailable [-9985]")
        return FakeInputStream.unstartable(error, **kwargs)

    def refuse_close(_stream: FakeInputStream) -> None:
        raise _FakePortAudioError("Error closing stream: Device unavailable [-9985]")

    monkeypatch.setattr(FakeInputStream, "close", refuse_close)
    _install(
        monkeypatch,
        RawInputStream=construct,
        query_devices=_device_info("USB Audio", 48000.0),
    )

    def callback(*_args: object) -> None:
        pass

    with pytest.raises(OSError, match="cannot open input device USB Audio") as failure:
        PortAudio(DeviceTable()).open(
            USB, samplerate=16000.0, blocksize=320, callback=callback
        )
    assert "Device unavailable" in str(failure.value)
    assert isinstance(failure.value.__cause__, _FakePortAudioError)
    assert "starting stream" in str(failure.value.__cause__)
    (line,) = [r.message for r in caplog.records if r.name == _DEVICE_MODULE]
    assert line == (
        "Input stream on USB Audio not closed after a start failure "
        "(_FakePortAudioError: Error closing stream: Device unavailable [-9985])"
    )


def test_a_device_query_that_fails_after_the_open_does_not_hide_the_start_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The device is passed in, read before the stream; the error path never asks.

    A device that was unplugged or grabbed is exactly when a second query
    fails, and the caller would otherwise be told "no default input device"
    with the real start error demoted to __context__.
    """
    constructed: list[FakeInputStream] = []

    def query_devices(kind: str) -> DeviceInfo:
        if constructed:  # The device went away once the stream existed.
            raise _FakePortAudioError("Error querying device input")
        return {"name": "USB Audio", "default_samplerate": 48000.0}

    def construct(**kwargs: object) -> FakeInputStream:
        error = _FakePortAudioError("start failed")
        stream = FakeInputStream.unstartable(error, **kwargs)
        constructed.append(stream)
        return stream

    _install(monkeypatch, RawInputStream=construct, query_devices=query_devices)

    def callback(*_args: object) -> None:
        pass

    with pytest.raises(OSError, match="cannot open input device USB Audio") as failure:
        PortAudio(DeviceTable()).open(
            USB, samplerate=16000.0, blocksize=320, callback=callback
        )
    assert "start failed" in str(failure.value)
    assert isinstance(failure.value.__cause__, _FakePortAudioError)
    assert str(failure.value.__cause__) == "start failed"


def test_devices_re_reads_the_table_and_reports_both_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeSoundDevice(
        input_name="MacBook Air Microphone", output_name="MacBook Air Speakers"
    ).install(monkeypatch)
    fake.connect("J's AirPods #1")
    assert PortAudio(DeviceTable()).devices() == DevicePair(
        InputDevice("J's AirPods #1", 48_000.0),
        OutputDevice("J's AirPods #1", 48_000.0),
    )
    assert fake.terminated == 1
    assert fake.initialized == 1


def test_a_refresh_portaudio_refuses_is_an_oserror_and_the_next_call_opens_audio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One failed initialize must not cost every later call until a restart.

    The launcher releases the call ID on an OSError only, so the library's
    error is converted; and the next call re-reads the table and opens its
    stream on the same process-wide table, with no daemon restart.
    """
    fake = FakeSoundDevice(input_name="Built-in", output_name="Built-in").install(
        monkeypatch
    )
    fake.fail_next_initialize("Error initializing: Host API not found")
    table = DeviceTable()
    backend = PortAudio(table)
    with pytest.raises(
        OSError, match="cannot re-read the audio device table"
    ) as failure:
        backend.devices()
    assert "Host API not found" in str(failure.value)
    assert isinstance(failure.value.__cause__, FakePortAudioError)
    fake.connect("Headset")
    devices = backend.devices()
    assert devices.input == InputDevice("Headset", 48_000.0)
    held = backend.open(
        devices.input, samplerate=16000.0, blocksize=320, callback=_ignore
    )
    (stream,) = fake.streams
    assert stream.device_name == "Headset"
    assert table.open_streams == 1
    held.__exit__(None, None, None)
    assert (fake.terminated, fake.initialized) == (1, 1)


def test_devices_keeps_the_table_while_a_stream_is_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Terminating PortAudio would close the open stream; the table waits."""
    fake = FakeSoundDevice(input_name="Built-in", output_name="Built-in").install(
        monkeypatch
    )
    table = DeviceTable()
    backend = PortAudio(table)
    held = backend.open(
        InputDevice("Built-in", 48_000.0),
        samplerate=16000.0,
        blocksize=320,
        callback=_ignore,
    )
    fake.connect("Headset")
    assert backend.devices() == DevicePair(
        InputDevice("Built-in", 48_000.0), OutputDevice("Built-in", 48_000.0)
    )
    assert fake.terminated == 0
    (stream,) = fake.streams
    assert not stream.killed
    held.__exit__(None, None, None)
    assert backend.devices().output == OutputDevice("Headset", 48_000.0)


def test_no_default_output_device_is_an_oserror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def query_devices(kind: str) -> DeviceInfo:
        if kind == "output":
            raise _FakePortAudioError("Error querying device output")
        return {"name": "USB Audio", "default_samplerate": 48000.0}

    _install(monkeypatch, query_devices=query_devices)
    with pytest.raises(OSError, match="no default output device") as failure:
        PortAudio.output_device()
    assert isinstance(failure.value.__cause__, _FakePortAudioError)


def test_an_output_device_reported_without_a_name_is_an_oserror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, query_devices=_device_info("", 48000.0))
    with pytest.raises(OSError, match="no usable default output device") as failure:
        PortAudio.output_device()
    assert isinstance(failure.value.__cause__, ValueError)
