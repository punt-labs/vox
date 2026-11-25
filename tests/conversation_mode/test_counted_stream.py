"""Tests for :mod:`punt_vox.voxd.conversation_mode.counted_stream`."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from punt_vox.voxd.conversation_mode.counted_stream import CountedStream
from punt_vox.voxd.conversation_mode.device_table import DeviceTable

if TYPE_CHECKING:
    from punt_vox.voxd.conversation_mode.input_device import InputDevice
    from punt_vox.voxd.conversation_mode.mic_audio_source import NativeInputStream


class _Stream:
    """A native stream that records its teardown calls."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __exit__(self, *exc_info: object) -> None:
        self.calls.append("exit")

    def stop(self, *, ignore_errors: bool = True) -> None:
        self.calls.append(f"stop({ignore_errors})")

    def close(self, *, ignore_errors: bool = True) -> None:
        self.calls.append(f"close({ignore_errors})")


class _Device:
    """A device whose strict release records the stream it was given, or refuses."""

    def __init__(self, error: OSError | None = None) -> None:  # None: release succeeds
        self.released_streams: list[object] = []
        self._error = error

    def released(self, stream: NativeInputStream) -> None:
        self.released_streams.append(stream)
        if self._error is not None:
            raise self._error


def _counted(device: _Device, stream: _Stream, table: DeviceTable) -> CountedStream:
    return CountedStream(
        cast("InputDevice", device), cast("NativeInputStream", stream), table.reserve()
    )


def test_leaving_tears_down_through_the_device_then_releases_the_count() -> None:
    table = DeviceTable()
    device, stream = _Device(), _Stream()
    counted = _counted(device, stream, table)
    assert table.open_streams == 1

    counted.__exit__(None, None, None)

    assert device.released_streams == [stream]
    assert stream.calls == []  # the device, not the counted stream, touches it
    assert table.open_streams == 0


def test_a_second_leave_does_nothing() -> None:
    table = DeviceTable()
    device, stream = _Device(), _Stream()
    counted = _counted(device, stream, table)

    counted.__exit__(None, None, None)
    counted.__exit__(None, None, None)

    assert device.released_streams == [stream]
    assert table.open_streams == 0


def test_a_refused_teardown_still_releases_the_count_and_is_raised() -> None:
    table = DeviceTable()
    device = _Device(OSError("cannot release input device Fake: unplugged"))
    counted = _counted(device, _Stream(), table)

    with pytest.raises(OSError, match="unplugged"):
        counted.__exit__(None, None, None)

    assert table.open_streams == 0
    counted.__exit__(None, None, None)  # and a later leave is still a no-op
    assert len(device.released_streams) == 1
