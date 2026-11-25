"""Tests for :mod:`punt_vox.voxd.conversation_mode.mic_capture`."""

from __future__ import annotations

import asyncio
import logging
from typing import Self, final

import pytest

from conversation_mode._fake_sounddevice import FakeSoundDevice
from conversation_mode._mic_fakes import FakeBackend, FakeInputStream
from punt_vox.voxd.conversation_mode.device_pair import DevicePair
from punt_vox.voxd.conversation_mode.input_device import InputDevice
from punt_vox.voxd.conversation_mode.mic_capture import MicCapture
from punt_vox.voxd.conversation_mode.output_device import OutputDevice
from punt_vox.voxd.conversation_mode.portaudio import PortAudio


async def test_open_holds_the_device_before_any_chunk_is_read() -> None:
    created: list[FakeInputStream] = []
    capture = MicCapture.open(FakeBackend(created), sample_rate_hz=16_000, chunk_s=0.02)
    (stream,) = created
    assert not stream.closed
    assert stream.kwargs["blocksize"] == 320
    capture.close()
    assert stream.closed


async def test_open_logs_a_device_name_with_a_newline_on_one_line(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The device names itself; its text cannot forge a call's end line."""

    with caplog.at_level(logging.INFO):
        capture = MicCapture.open(
            _NamedBackend("USB\nCall ended: forged", "USB\nCall ended: forged"),
            sample_rate_hz=16_000,
            chunk_s=0.02,
        )
    capture.close()
    (line,) = [r.message for r in caplog.records if "input device" in r.message]
    assert line == (
        "Call input device: USB Call ended: forged (default 48000 Hz), "
        "capturing at 16000 Hz"
    )
    (line,) = [r.message for r in caplog.records if "output device" in r.message]
    assert line == "Call output device: USB Call ended: forged (default 44100 Hz)"


@final
class _NamedBackend:
    """A backend reporting the given input and output device names."""

    __slots__ = ("_devices",)
    _devices: DevicePair

    def __new__(cls, input_name: str, output_name: str) -> Self:
        self = super().__new__(cls)
        self._devices = DevicePair(
            InputDevice(input_name, 48_000.0), OutputDevice(output_name, 44_100.0)
        )
        return self

    def devices(self) -> DevicePair:
        return self._devices

    def open(self, device: InputDevice, **kwargs: object) -> FakeInputStream:
        assert device == self._devices.input
        return FakeInputStream(**kwargs)


async def test_open_logs_the_echo_configuration_when_the_legs_are_different_hardware(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A built-in microphone with Bluetooth output: the speakers feed the mic."""
    with caplog.at_level(logging.INFO):
        capture = MicCapture.open(
            _NamedBackend("MacBook Air Microphone", "J's AirPods #1"),
            sample_rate_hz=16_000,
            chunk_s=0.02,
        )
    capture.close()
    (record,) = [r for r in caplog.records if "echo configuration" in r.message]
    assert record.levelno == logging.WARNING
    assert record.message == (
        "Call echo configuration: input MacBook Air Microphone and output "
        "J's AirPods #1 are different hardware, so the speaker output can reach "
        "the microphone"
    )


@pytest.mark.parametrize(
    ("input_name", "output_name", "hardware"),
    [
        ("J's AirPods #1", "J's AirPods #1", "j's airpods #1"),
        ("MacBook Air Microphone", "MacBook Air Speakers", "macbook air"),
    ],
)
async def test_open_logs_one_piece_of_hardware_and_no_echo_configuration(
    caplog: pytest.LogCaptureFixture, input_name: str, output_name: str, hardware: str
) -> None:
    with caplog.at_level(logging.INFO):
        capture = MicCapture.open(
            _NamedBackend(input_name, output_name), sample_rate_hz=16_000, chunk_s=0.02
        )
    capture.close()
    assert not [r for r in caplog.records if "echo configuration" in r.message]
    assert [r.message for r in caplog.records if "output device" in r.message] == [
        f"Call output device: {output_name} (default 44100 Hz)"
    ]
    (record,) = [r for r in caplog.records if "audio hardware" in r.message]
    assert record.levelno == logging.INFO
    assert record.message == (
        f"Call audio hardware: input and output are the same hardware ({hardware})"
    )


async def test_open_propagates_a_device_refusal_and_holds_nothing() -> None:
    created: list[FakeInputStream] = []

    def refuse(**_kwargs: object) -> FakeInputStream:
        raise OSError("cannot open input device")

    with pytest.raises(OSError, match="cannot open input device"):
        MicCapture.open(
            FakeBackend(created, open_stream=refuse),
            sample_rate_hz=16_000,
            chunk_s=0.02,
        )
    assert created == []


async def test_chunks_yield_fed_pcm_with_the_configured_duration() -> None:
    created: list[FakeInputStream] = []
    capture = MicCapture.open(FakeBackend(created), sample_rate_hz=16_000, chunk_s=0.05)
    gen = capture.chunks()
    task = asyncio.ensure_future(gen.__anext__())
    await asyncio.sleep(0)
    created[0].feed(b"\x01\x00")
    chunk = await task
    assert chunk.pcm == b"\x01\x00"
    assert chunk.duration_s == pytest.approx(0.05)
    await gen.aclose()
    capture.close()


async def test_blocks_captured_before_the_reader_starts_are_discarded() -> None:
    """The device is held from open(); what it heard before pickup is not sent."""
    created: list[FakeInputStream] = []
    capture = MicCapture.open(FakeBackend(created), sample_rate_hz=16_000, chunk_s=0.02)
    created[0].feed(b"stale")
    await asyncio.sleep(0)  # the callback's put reaches the loop
    gen = capture.chunks()
    task = asyncio.ensure_future(gen.__anext__())
    await asyncio.sleep(0)
    assert not task.done()
    created[0].feed(b"fresh")
    assert (await task).pcm == b"fresh"
    await gen.aclose()
    capture.close()


async def test_a_reader_that_falls_behind_sees_only_the_newest_second(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """At 20 ms blocks the queue holds one second: 50 blocks, oldest dropped."""
    created: list[FakeInputStream] = []
    capture = MicCapture.open(FakeBackend(created), sample_rate_hz=16_000, chunk_s=0.02)
    gen = capture.chunks()
    task = asyncio.ensure_future(gen.__anext__())
    await asyncio.sleep(0)
    created[0].feed(b"first")
    assert (await task).pcm == b"first"
    for index in range(60):
        created[0].feed(str(index).encode())
    with caplog.at_level(logging.WARNING):
        chunk = await gen.__anext__()
    assert chunk.pcm == b"10"
    lines = [r.message for r in caplog.records if "dropped" in r.message]
    assert lines == ["mic capture: dropped 10 blocks the reader had not taken"]
    await gen.aclose()
    capture.close()


async def test_a_call_opened_after_a_headset_connects_captures_from_the_headset(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A long-running voxd must not capture from the device table it
    enumerated at startup once the system's default input has changed."""
    fake = FakeSoundDevice(
        input_name="MacBook Air Microphone", output_name="MacBook Air Speakers"
    ).install(monkeypatch)
    fake.connect("J's AirPods #1")
    with caplog.at_level(logging.INFO):
        capture = MicCapture.open(PortAudio(), sample_rate_hz=16_000, chunk_s=0.02)
    capture.close()
    (stream,) = fake.streams
    assert stream.device_name == "J's AirPods #1"
    (line,) = [r.message for r in caplog.records if "input device" in r.message]
    assert line.startswith("Call input device: J's AirPods #1")
