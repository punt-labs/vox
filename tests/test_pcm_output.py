"""Tests for the PortAudio output adapter the PCM sink drives in voxd."""

from __future__ import annotations

import sys
import types
from typing import TYPE_CHECKING, Self, cast, final

import pytest

from punt_vox.voxd.pcm_output import sounddevice_output_stream

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
