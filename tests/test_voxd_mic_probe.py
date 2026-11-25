"""Tests for punt_vox.voxd.mic_probe -- the daemon-side microphone probe RPC.

No test opens an audio device: the handler takes an injectable capture
function and every test supplies a fake.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import TYPE_CHECKING, Literal, NoReturn, cast

import pytest
from conversation_mode._fake_sounddevice import FakeSoundDevice
from conversation_mode._mic_fakes import (
    FAKE_DEVICE,
    FakePortAudioError,
    fake_sounddevice,
)

from punt_vox.types_mic import MicProbeReport
from punt_vox.voxd.conversation_mode.device_table import DeviceTable
from punt_vox.voxd.conversation_mode.portaudio import PortAudio
from punt_vox.voxd.mic_probe import MicProbeError, MicProbeHandler, SoundDeviceCapture

if TYPE_CHECKING:
    from collections.abc import Callable

    from sounddevice import DeviceInfo
    from starlette.websockets import WebSocket

    from punt_vox.voxd.conversation_mode.input_device import InputDevice


class _CollectingWs:
    """A fake websocket that records the frames the handler sends."""

    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []

    async def send_json(self, data: dict[str, object]) -> None:
        self.sent.append(data)


def _report(peak: int) -> MicProbeReport:
    return MicProbeReport(
        device="Fake Mic", sample_rate_hz=16_000, peak=peak, samples=16_000
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("peak", [0, 812])
async def test_probe_replies_with_the_capture_report(peak: int) -> None:
    """Silence and signal both cross as a ``mic_result`` carrying the report."""
    seconds_seen: list[float] = []

    def capture(seconds: float) -> MicProbeReport:
        seconds_seen.append(seconds)
        return _report(peak)

    ws = _CollectingWs()
    await MicProbeHandler(capture=capture)(
        {"type": "mic_probe", "id": "m1"}, cast("WebSocket", ws)
    )

    assert seconds_seen == [1.0]
    (frame,) = ws.sent
    assert frame["type"] == "mic_result"
    assert frame["id"] == "m1"
    report = MicProbeReport.from_wire(frame["result"])
    assert report == _report(peak)
    assert report.signal == (peak > 0)


@pytest.mark.asyncio
async def test_unopenable_device_crosses_as_an_error_frame() -> None:
    """A device the daemon cannot open answers with the error text, not a teardown."""

    def capture(_seconds: float) -> MicProbeReport:
        raise MicProbeError("Error opening RawInputStream: Internal PortAudio error")

    ws = _CollectingWs()
    await MicProbeHandler(capture=capture)(
        {"type": "mic_probe", "id": "m2"}, cast("WebSocket", ws)
    )

    (frame,) = ws.sent
    assert frame["type"] == "error"
    assert frame["id"] == "m2"
    assert "Internal PortAudio error" in str(frame["message"])


@pytest.mark.asyncio
async def test_missing_call_extra_names_the_fix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without sounddevice the default capture names the extra instead of crashing."""
    monkeypatch.setitem(sys.modules, "sounddevice", None)
    with pytest.raises(MicProbeError, match=r"punt-vox\[call\]"):
        SoundDeviceCapture()(0.01)


class _RecordingStream:
    """A started stream handle that records when it is left."""

    def __init__(self, events: list[str]) -> None:
        self._events = events

    def __exit__(self, *exc_info: object) -> None:
        self._events.append("exit")


class _RecordingBackend:
    """A probe backend that records the order of its calls."""

    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.kwargs: dict[str, object] = {}

    def open_default_input(
        self, **kwargs: object
    ) -> tuple[InputDevice, _RecordingStream]:
        self.events.append("open_default_input")
        self.kwargs = kwargs
        return FAKE_DEVICE, _RecordingStream(self.events)


def _install_sleep(
    monkeypatch: pytest.MonkeyPatch, on_sleep: Callable[[int], None]
) -> None:
    """Install a ``sounddevice`` module whose ``sleep`` is *on_sleep*."""
    module = SimpleNamespace(sleep=on_sleep, PortAudioError=FakePortAudioError)
    monkeypatch.setitem(sys.modules, "sounddevice", module)


def test_capture_refreshes_the_device_table_then_holds_the_stream_while_sleeping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The probe opens the current default input in one backend call, then holds it."""
    events: list[str] = []
    backend = _RecordingBackend(events)

    def sleep(msec: int) -> None:
        events.append(f"sleep {msec}")

    _install_sleep(monkeypatch, sleep)
    report = SoundDeviceCapture(backend)(0.25)

    assert events == ["open_default_input", "sleep 250", "exit"]
    assert backend.kwargs["samplerate"] == 16_000.0
    assert backend.kwargs["blocksize"] == 0
    assert report.device == FAKE_DEVICE.name
    assert report.samples == 0


def test_capture_reserves_the_stream_on_the_device_table_for_its_duration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Through the real backend, a refresh cannot terminate under the probe."""
    table = DeviceTable()
    backend = fake_sounddevice(monkeypatch)
    seen: list[int] = []

    def sleep(_msec: int) -> None:
        seen.append(table.open_streams)
        backend.created[-1].feed(b"\x10\x00\x00\x00")

    sys.modules["sounddevice"].sleep = sleep  # type: ignore[attr-defined]  # the fake module is a namespace
    report = SoundDeviceCapture(PortAudio(table))(1.0)

    assert seen == [1]
    assert table.open_streams == 0
    assert backend.created[-1].closed
    assert report == MicProbeReport(
        device=FAKE_DEVICE.name, sample_rate_hz=16_000, peak=16, samples=2
    )


@pytest.mark.parametrize(
    "error",
    [
        OSError("cannot open input device Fake Microphone: busy"),
        UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"),
    ],
)
def test_backend_refusals_become_mic_probe_errors(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    """A device the backend refuses or cannot describe is a probe error."""

    class _Refusing(_RecordingBackend):
        def open_default_input(self, **_kwargs: object) -> NoReturn:
            raise error

    _install_sleep(monkeypatch, lambda _msec: None)
    with pytest.raises(MicProbeError, match=str(error)):
        SoundDeviceCapture(_Refusing([]))(0.01)


@pytest.mark.asyncio
async def test_decode_error_in_the_default_capture_is_an_error_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The handler's default capture turns a decode error into an error frame."""

    class _Undescribable(_RecordingBackend):
        def open_default_input(self, **_kwargs: object) -> NoReturn:
            raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    _install_sleep(monkeypatch, lambda _msec: None)
    ws = _CollectingWs()
    await MicProbeHandler(capture=SoundDeviceCapture(_Undescribable([])))(
        {"type": "mic_probe", "id": "m3"}, cast("WebSocket", ws)
    )

    (frame,) = ws.sent
    assert frame["type"] == "error"
    assert "invalid start byte" in str(frame["message"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("peak", "samples", "level"),
    [(812, 16_000, "INFO"), (0, 16_000, "WARNING"), (0, 0, "WARNING")],
)
async def test_probe_logs_a_silent_or_empty_capture_as_a_warning(
    caplog: pytest.LogCaptureFixture, peak: int, samples: int, level: str
) -> None:
    """A healthy probe is INFO; one that heard nothing is a WARNING in vox.log."""

    def capture(_seconds: float) -> MicProbeReport:
        return MicProbeReport(
            device="Fake Mic", sample_rate_hz=16_000, peak=peak, samples=samples
        )

    with caplog.at_level("INFO", logger="punt_vox.voxd.mic_probe"):
        await MicProbeHandler(capture=capture)(
            {"type": "mic_probe", "id": "m4"}, cast("WebSocket", _CollectingWs())
        )

    (record,) = [r for r in caplog.records if r.name == "punt_vox.voxd.mic_probe"]
    assert record.levelname == level
    assert f"peak={peak}" in record.getMessage()
    assert f"samples={samples}" in record.getMessage()


def test_capture_reports_the_device_it_captured_from_when_the_default_moves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A default that changes while the probe runs cannot split name from stream."""
    fake = FakeSoundDevice(input_name="Built-in", output_name="Speakers").install(
        monkeypatch
    )
    table = DeviceTable()
    real_query = fake.query_devices

    def query_then_reconnect(
        kind: Literal["input", "output"] | None = None,  # None: the whole table
    ) -> DeviceInfo | list[DeviceInfo]:
        if kind is None:  # the refresh's log summary counts every device
            return real_query()
        info = real_query(kind)
        if table.open_streams == 0:  # the refresh's own summary, under its lock
            return info
        fake.set_system_default("input", "Headset")
        table.refresh()
        return info

    def sleep(_msec: int) -> None:
        fake.streams[-1].feed(b"\x05\x00")

    module = sys.modules["sounddevice"]
    monkeypatch.setattr(module, "query_devices", query_then_reconnect)
    monkeypatch.setattr(module, "sleep", sleep, raising=False)
    report = SoundDeviceCapture(PortAudio(table))(1.0)

    assert report.device == fake.streams[-1].device_name == "Built-in"
    assert report.peak == 5


@pytest.mark.parametrize(
    "error",
    [
        FakePortAudioError("Error closing stream: Internal PortAudio error"),
        OSError("cannot release input device Fake Microphone"),
    ],
)
def test_a_stream_that_will_not_close_is_a_mic_probe_error(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    """A close that fails is the device's error, not a lost connection."""

    class _Unclosable(_RecordingStream):
        def __exit__(self, *exc_info: object) -> None:
            raise error

    class _Backend(_RecordingBackend):
        def open_default_input(
            self, **kwargs: object
        ) -> tuple[InputDevice, _RecordingStream]:
            return FAKE_DEVICE, _Unclosable(self.events)

    _install_sleep(monkeypatch, lambda _msec: None)
    with pytest.raises(MicProbeError, match=str(error)):
        SoundDeviceCapture(_Backend([]))(0.01)
