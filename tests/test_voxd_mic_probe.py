"""Tests for punt_vox.voxd.mic_probe -- the daemon-side microphone probe RPC.

No test opens an audio device: the handler takes an injectable capture
function and every test supplies a fake.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest
from conversation_mode._mic_fakes import (
    FAKE_DEVICE,
    FAKE_OUTPUT,
    FakePortAudioError,
    fake_sounddevice,
)

from punt_vox.types_mic import MicProbeReport
from punt_vox.voxd.conversation_mode.device_pair import DevicePair
from punt_vox.voxd.conversation_mode.device_table import DeviceTable
from punt_vox.voxd.conversation_mode.portaudio import PortAudio
from punt_vox.voxd.mic_probe import MicProbeError, MicProbeHandler, SoundDeviceCapture

if TYPE_CHECKING:
    from collections.abc import Callable

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
    """A capture backend that records the order of its calls."""

    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.kwargs: dict[str, object] = {}

    def devices(self) -> DevicePair:
        self.events.append("devices")
        return DevicePair(FAKE_DEVICE, FAKE_OUTPUT)

    def open(self, device: InputDevice, **kwargs: object) -> _RecordingStream:
        assert device == FAKE_DEVICE
        self.events.append("open")
        self.kwargs = kwargs
        return _RecordingStream(self.events)


def _install_sleep(
    monkeypatch: pytest.MonkeyPatch, on_sleep: Callable[[int], None]
) -> None:
    """Install a ``sounddevice`` module whose ``sleep`` is *on_sleep*."""
    module = SimpleNamespace(sleep=on_sleep, PortAudioError=FakePortAudioError)
    monkeypatch.setitem(sys.modules, "sounddevice", module)


def test_capture_refreshes_the_device_table_then_holds_the_stream_while_sleeping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The probe asks the backend for the current default, then opens and holds it."""
    events: list[str] = []
    backend = _RecordingBackend(events)

    def sleep(msec: int) -> None:
        events.append(f"sleep {msec}")

    _install_sleep(monkeypatch, sleep)
    report = SoundDeviceCapture(backend)(0.25)

    assert events == ["devices", "open", "sleep 250", "exit"]
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
        def devices(self) -> DevicePair:
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
        def devices(self) -> DevicePair:
            raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    _install_sleep(monkeypatch, lambda _msec: None)
    ws = _CollectingWs()
    await MicProbeHandler(capture=SoundDeviceCapture(_Undescribable([])))(
        {"type": "mic_probe", "id": "m3"}, cast("WebSocket", ws)
    )

    (frame,) = ws.sent
    assert frame["type"] == "error"
    assert "invalid start byte" in str(frame["message"])
