"""Tests for :mod:`punt_vox.voxd.conversation_mode.counted_output`."""

from __future__ import annotations

from typing import TYPE_CHECKING, Self, final

import pytest

from conversation_mode._fake_sounddevice import FakeSoundDevice
from punt_vox.voxd.conversation_mode.counted_output import (
    CountedOutput,
    CountedOutputStream,
)
from punt_vox.voxd.conversation_mode.device_table import DeviceTable

if TYPE_CHECKING:
    from collections.abc import Callable

    from punt_vox.voxd.pcm_output import OutputCallback


@final
class _Stream:
    """An output stream recording the calls made on it."""

    __slots__ = ("_calls", "_close_error", "_start_error")
    _calls: list[str]
    _close_error: Exception | None  # None: close succeeds; else raised by close.
    _start_error: Exception | None  # None: start succeeds; else raised by start.

    def __new__(
        cls,
        start_error: Exception | None = None,  # None: the stream starts.
        close_error: Exception | None = None,  # None: the stream closes.
    ) -> Self:
        self = super().__new__(cls)
        self._calls = []
        self._start_error = start_error
        self._close_error = close_error
        return self

    @property
    def calls(self) -> list[str]:
        """The calls made on the stream, in order."""
        return list(self._calls)

    def start(self) -> None:
        if self._start_error is not None:
            raise self._start_error
        self._calls.append("start")

    def stop(self) -> None:
        self._calls.append("stop")

    def abort(self) -> None:
        self._calls.append("abort")

    def close(self) -> None:
        if self._close_error is not None:
            raise self._close_error
        self._calls.append("close")


@final
class _Factory:
    """An output stream factory returning one stream and recording its arguments."""

    __slots__ = ("_kwargs", "_stream")
    _kwargs: dict[str, object]
    _stream: _Stream

    def __new__(cls, stream: _Stream) -> Self:
        self = super().__new__(cls)
        self._stream = stream
        self._kwargs = {}
        return self

    @property
    def kwargs(self) -> dict[str, object]:
        """The keyword arguments of the last call, empty before any."""
        return dict(self._kwargs)

    def __call__(
        self,
        *,
        samplerate: int,
        blocksize: int,
        callback: OutputCallback,
        finished_callback: Callable[[], None],
    ) -> _Stream:
        self._kwargs = {
            "samplerate": samplerate,
            "blocksize": blocksize,
            "callback": callback,
            "finished_callback": finished_callback,
        }
        return self._stream


def _ignore(*_args: object) -> None:
    pass


def _open(factory: _Factory, table: DeviceTable) -> CountedOutputStream:
    return CountedOutput(factory, table)(
        samplerate=16_000, blocksize=320, callback=_ignore, finished_callback=_ignore
    )


def test_the_factory_receives_every_argument() -> None:
    factory = _Factory(_Stream())
    stream = _open(factory, DeviceTable())
    assert isinstance(stream, CountedOutputStream)
    assert factory.kwargs == {
        "samplerate": 16_000,
        "blocksize": 320,
        "callback": _ignore,
        "finished_callback": _ignore,
    }


def test_the_table_is_re_read_before_the_stream_opens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeSoundDevice(input_name="Built-in", output_name="Built-in").install(
        monkeypatch
    )
    fake.connect("Headset")
    _open(_Factory(_Stream()), DeviceTable())
    assert fake.terminated == 1
    assert fake.query_devices("output")["name"] == "Headset"


def test_a_stream_is_counted_from_its_creation_until_closed() -> None:
    inner = _Stream()
    table = DeviceTable()
    stream = _open(_Factory(inner), table)
    assert table.open_streams == 1
    stream.start()
    assert table.open_streams == 1
    stream.stop()
    stream.abort()
    stream.close()
    assert table.open_streams == 0
    stream.close()  # Idempotent, like the stream itself.
    assert table.open_streams == 0
    assert inner.calls == ["start", "stop", "abort", "close", "close"]


def test_a_stream_that_fails_to_start_gives_its_reservation_back() -> None:
    table = DeviceTable()
    stream = _open(_Factory(_Stream(RuntimeError("device busy"))), table)
    assert table.open_streams == 1
    with pytest.raises(RuntimeError, match="device busy"):
        stream.start()
    assert table.open_streams == 0
    stream.close()  # The sink closes a stream that would not start.
    assert table.open_streams == 0


def test_a_factory_that_fails_gives_the_reservation_back() -> None:
    table = DeviceTable()

    def refuse(**_kwargs: object) -> _Stream:
        raise RuntimeError("no output device")

    with pytest.raises(RuntimeError, match="no output device"):
        CountedOutput(refuse, table)(
            samplerate=16_000,
            blocksize=320,
            callback=_ignore,
            finished_callback=_ignore,
        )
    assert table.open_streams == 0


def test_the_count_is_raised_before_the_native_stream_is_created(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P1 of docs/device-table.tex: refresh never terminates PortAudio while a
    stream is reserved.

    A refresh on another thread that lands while the factory is creating
    the native stream must find the count already raised; otherwise it
    would terminate PortAudio underneath a stream that exists but is not
    yet counted.
    """
    fake = FakeSoundDevice(input_name="Built-in", output_name="Built-in").install(
        monkeypatch
    )
    table = DeviceTable()
    refreshed: list[bool] = []

    def create(**_kwargs: object) -> _Stream:
        fake.connect("Headset")  # A device change arrives mid-creation.
        refreshed.append(table.refresh())
        return _Stream()

    stream = CountedOutput(create, table)(
        samplerate=16_000, blocksize=320, callback=_ignore, finished_callback=_ignore
    )
    assert refreshed == [False]
    assert fake.terminated == 1  # The refresh before creation, none during it.
    stream.close()
    assert table.open_streams == 0


def test_the_count_is_released_even_when_close_fails() -> None:
    table = DeviceTable()
    stream = _open(_Factory(_Stream(close_error=RuntimeError("close failed"))), table)
    stream.start()
    with pytest.raises(RuntimeError, match="close failed"):
        stream.close()
    assert table.open_streams == 0
