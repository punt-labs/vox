"""A fake ``sounddevice`` module that enumerates devices the way PortAudio does.

PortAudio reads the system's device table once, in ``Pa_Initialize``, and
answers every later query from that copy; only ``Pa_Terminate`` followed by
``Pa_Initialize`` re-reads it. This fake keeps the same two layers: the
*system* defaults a test may change at any time, and the *table* copied from
them at each initialization, which is what ``query_devices`` and the stream
constructors see. A stream records the device name the table held when it
opened, which is the device PortAudio would have bound it to.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import TYPE_CHECKING, Literal, Self, final, overload

from conversation_mode._mic_fakes import FakeCallbackFlags

__all__ = ["FakeDeviceStream", "FakeSoundDevice"]

if TYPE_CHECKING:
    import pytest
    from sounddevice import DeviceInfo

type Kind = Literal["input", "output"]


class _FakePortAudioError(Exception):
    pass


@final
class FakeDeviceStream:
    """A stream bound to one device: the table's default when it was opened.

    Serves as both ``RawInputStream`` (entered to start) and
    ``RawOutputStream`` (started explicitly), recording the keyword
    arguments and whether a terminate killed it while open.
    """

    __slots__ = ("_closed", "_device_name", "_killed", "_kwargs", "_started")
    _closed: bool
    _device_name: str
    _killed: bool
    _kwargs: dict[str, object]
    _started: bool

    def __new__(cls, device_name: str, **kwargs: object) -> Self:
        self = super().__new__(cls)
        self._device_name = device_name
        self._kwargs = kwargs
        self._closed = self._killed = self._started = False
        return self

    @property
    def device_name(self) -> str:
        """The device this stream was opened on."""
        return self._device_name

    @property
    def kwargs(self) -> dict[str, object]:
        return dict(self._kwargs)

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def killed(self) -> bool:
        """Whether PortAudio was terminated while this stream was open."""
        return self._killed

    def kill(self) -> None:
        if not self._closed:
            self._killed = True

    def feed(self, pcm: bytes) -> None:
        """Invoke the registered input callback with *pcm*, as PortAudio would."""
        callback = self._kwargs["callback"]
        assert callable(callback)
        callback(pcm, len(pcm) // 2, None, FakeCallbackFlags())

    def start(self) -> None:
        self._started = True

    def stop(self) -> None:
        self._started = False

    def abort(self) -> None:
        self._started = False

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self._closed = True


@final
class FakeSoundDevice:
    """The fake module's state: system defaults, the enumerated table, streams."""

    __slots__ = ("_initialized", "_streams", "_system", "_table", "_terminated")
    _initialized: int
    _streams: list[FakeDeviceStream]
    _system: dict[Kind, str]
    _table: dict[Kind, str]
    _terminated: int

    def __new__(cls, *, input_name: str, output_name: str) -> Self:
        """Start with PortAudio initialized once, as ``import sounddevice`` does."""
        self = super().__new__(cls)
        self._system = {"input": input_name, "output": output_name}
        self._table = dict(self._system)
        self._streams = []
        self._initialized = 1
        self._terminated = 0
        return self

    def install(self, monkeypatch: pytest.MonkeyPatch) -> Self:
        """Put the fake in ``sys.modules`` as ``sounddevice``."""
        module = SimpleNamespace(
            PortAudioError=_FakePortAudioError,
            RawInputStream=self.raw_input_stream,
            RawOutputStream=self.raw_output_stream,
            query_devices=self.query_devices,
            _initialize=self.initialize,
            _terminate=self.terminate,
        )
        monkeypatch.setitem(sys.modules, "sounddevice", module)
        return self

    def connect(self, name: str) -> None:
        """The system now routes both input and output to *name* (a headset)."""
        self._system = {"input": name, "output": name}

    def set_system_default(self, kind: Kind, name: str) -> None:
        self._system[kind] = name

    @property
    def streams(self) -> list[FakeDeviceStream]:
        """Every stream opened, oldest first."""
        return self._streams

    @property
    def terminated(self) -> int:
        """How many times PortAudio was terminated."""
        return self._terminated

    @property
    def initialized(self) -> int:
        """PortAudio's initialization count (one after import)."""
        return self._initialized

    @overload
    def query_devices(self) -> list[DeviceInfo]: ...
    @overload
    def query_devices(self, kind: Kind) -> DeviceInfo: ...
    def query_devices(self, kind: Kind | None = None) -> DeviceInfo | list[DeviceInfo]:
        """The table's default of *kind*, or every enumerated device (input, output)."""
        if kind is None:
            return [self._info("input"), self._info("output")]
        return self._info(kind)

    def _info(self, kind: Kind) -> DeviceInfo:
        return {
            "name": self._table[kind],
            "index": 0 if kind == "input" else 1,
            "default_samplerate": 48_000.0,
        }

    def raw_input_stream(self, **kwargs: object) -> FakeDeviceStream:
        return self._open("input", **kwargs)

    def raw_output_stream(self, **kwargs: object) -> FakeDeviceStream:
        return self._open("output", **kwargs)

    def terminate(self) -> None:
        """``Pa_Terminate``: closes every open stream, invalidating it."""
        for stream in self._streams:
            stream.kill()
        self._terminated += 1
        self._initialized -= 1

    def initialize(self) -> None:
        """``Pa_Initialize``: enumerate the system's devices afresh."""
        self._table = dict(self._system)
        self._initialized += 1

    def _open(self, kind: Kind, **kwargs: object) -> FakeDeviceStream:
        stream = FakeDeviceStream(self._table[kind], **kwargs)
        self._streams.append(stream)
        return stream
