"""Tests for the PortAudio output adapter the PCM sink drives in voxd."""

from __future__ import annotations

import asyncio
import sys
import threading
import types
from typing import TYPE_CHECKING, Self, cast, final

import pytest

from punt_vox.voxd.pcm_output import DeviceCall, sounddevice_output_stream

if TYPE_CHECKING:
    from collections.abc import Buffer


@final
class RecordingRawOutputStream:
    """Stand-in for ``sounddevice.RawOutputStream`` that keeps its arguments."""

    __slots__ = ("kwargs",)

    kwargs: dict[str, object]

    def __new__(cls, **kwargs: object) -> Self:
        self = super().__new__(cls)
        self.kwargs = kwargs
        return self


def _callback(_out: Buffer, _frames: int, _time: object, _status: object) -> None:
    """Output callback placeholder."""


def _finished() -> None:
    """Finished-callback placeholder."""


class TestSounddeviceOutputStream:
    def test_opens_a_low_latency_mono_int16_stream(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = types.ModuleType("sounddevice")
        fake.__dict__["RawOutputStream"] = RecordingRawOutputStream
        monkeypatch.setitem(sys.modules, "sounddevice", fake)

        stream = sounddevice_output_stream(
            samplerate=16_000,
            blocksize=320,
            callback=_callback,
            finished_callback=_finished,
        )

        assert cast("RecordingRawOutputStream", stream).kwargs == {
            "samplerate": 16_000,
            "blocksize": 320,
            "channels": 1,
            "dtype": "int16",
            "latency": "low",
            "callback": _callback,
            "finished_callback": _finished,
        }

    def test_missing_call_extra_names_the_fix(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setitem(sys.modules, "sounddevice", None)

        with pytest.raises(RuntimeError, match=r"punt-vox\[call\]") as caught:
            sounddevice_output_stream(
                samplerate=16_000,
                blocksize=320,
                callback=_callback,
                finished_callback=_finished,
            )
        assert isinstance(caught.value.__cause__, ImportError)


def _fail() -> None:
    msg = "device busy"
    raise OSError(msg)


class TestDeviceCall:
    async def test_returns_the_thread_result(self) -> None:
        call = await DeviceCall.run(sum, (1, 2, 3))
        assert call.result() == 6
        assert call.error() is None
        assert not call.cancelled

    async def test_keeps_the_thread_error(self) -> None:
        call = await DeviceCall.run(_fail)
        assert isinstance(call.error(), OSError)
        with pytest.raises(OSError, match="device busy"):
            call.result()

    async def test_waits_out_every_cancellation(self) -> None:
        release = threading.Event()
        finished = threading.Event()

        def blocking() -> str:
            release.wait(timeout=5)
            finished.set()
            return "released"

        running = asyncio.create_task(DeviceCall.run(blocking))
        for _ in range(3):
            await asyncio.sleep(0.01)
            running.cancel()
        await asyncio.sleep(0.01)
        assert not running.done(), "a cancel abandoned the device thread"
        release.set()
        call = await running
        assert finished.is_set()
        assert call.cancelled
        assert call.result() == "released"
