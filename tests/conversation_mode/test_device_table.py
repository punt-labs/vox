"""Tests for :mod:`punt_vox.voxd.conversation_mode.device_table`."""

from __future__ import annotations

import logging
import sys

import pytest

from conversation_mode._fake_sounddevice import FakeSoundDevice
from punt_vox.voxd.conversation_mode.device_table import DeviceTable


def test_refresh_re_reads_the_table_when_no_stream_is_open(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    fake = FakeSoundDevice(input_name="Built-in", output_name="Built-in").install(
        monkeypatch
    )
    fake.connect("Headset")
    with caplog.at_level(logging.INFO):
        assert DeviceTable().refresh() is True
    assert fake.query_devices("output")["name"] == "Headset"
    assert (fake.terminated, fake.initialized) == (1, 1)
    (line,) = caplog.messages
    assert line == (
        "PortAudio device table re-read from the system: 2 devices, "
        "default input #0, default output #1"
    )


def test_refresh_is_a_no_op_before_sounddevice_is_imported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An import that has not happened will enumerate fresh; nothing is stale."""
    monkeypatch.delitem(sys.modules, "sounddevice", raising=False)
    assert DeviceTable().refresh() is False


def test_refresh_keeps_the_table_while_a_stream_is_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeSoundDevice(input_name="Built-in", output_name="Built-in").install(
        monkeypatch
    )
    table = DeviceTable()
    table.stream_opened()
    fake.connect("Headset")
    assert table.refresh() is False
    assert fake.terminated == 0
    assert fake.query_devices("input")["name"] == "Built-in"
    table.stream_closed()
    assert table.refresh() is True
    assert fake.query_devices("input")["name"] == "Headset"


def test_open_streams_counts_up_and_down() -> None:
    table = DeviceTable()
    assert table.open_streams == 0
    table.stream_opened()
    table.stream_opened()
    assert table.open_streams == 2
    table.stream_closed()
    assert table.open_streams == 1


def test_closing_a_stream_that_was_never_opened_is_a_bug() -> None:
    with pytest.raises(ValueError, match="no PortAudio stream is open"):
        DeviceTable().stream_closed()


def test_the_shared_table_is_one_object_per_process() -> None:
    assert DeviceTable.shared() is DeviceTable.shared()
    assert DeviceTable() is not DeviceTable.shared()
