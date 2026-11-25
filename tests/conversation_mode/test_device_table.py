"""Tests for :mod:`punt_vox.voxd.conversation_mode.device_table`."""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING

import pytest

from conversation_mode._fake_sounddevice import FakePortAudioError, FakeSoundDevice
from punt_vox.voxd.conversation_mode.device_table import DeviceTable

if TYPE_CHECKING:
    from sounddevice import DeviceInfo

    from conversation_mode._fake_sounddevice import Kind


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


def test_refresh_reports_a_device_it_cannot_describe_and_still_re_reads(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A device the library cannot describe is unrelated to the call placed.

    Counting the devices decodes every name; one it cannot decode must not
    keep the call from the devices it can use.
    """
    fake = FakeSoundDevice(input_name="Built-in", output_name="Built-in").install(
        monkeypatch
    )
    module = sys.modules["sounddevice"]

    def query_devices(kind: Kind | None = None) -> DeviceInfo | list[DeviceInfo]:
        if kind is None:  # Describing the whole table reaches the bad name.
            raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
        return fake.query_devices(kind)

    monkeypatch.setattr(module, "query_devices", query_devices)
    fake.connect("Headset")
    with caplog.at_level(logging.INFO):
        assert DeviceTable().refresh() is True
    assert fake.query_devices("output")["name"] == "Headset"
    (line,) = caplog.messages
    assert line == (
        "PortAudio device table re-read from the system: device count unavailable "
        "(UnicodeDecodeError: 'utf-8' codec can't decode byte 0xff in position 0: "
        "invalid start byte), default input #0, default output #1"
    )


def test_refresh_terminates_portaudio_only_while_it_is_initialized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P2 of docs/device-table.tex: terminate only while initialized.

    A failed initialize leaves the library terminated. A refresh that
    terminated it again would fail there, every time, until the daemon
    restarted; the next refresh initializes directly instead and the table
    is read again.
    """
    fake = FakeSoundDevice(input_name="Built-in", output_name="Built-in").install(
        monkeypatch
    )
    fake.fail_next_initialize("Error initializing: Host API not found")
    table = DeviceTable()
    assert (table.initialized, fake.terminated, fake.initialized) == (True, 0, 1)
    with pytest.raises(FakePortAudioError, match="Host API not found"):
        table.refresh()
    assert (table.initialized, fake.terminated, fake.initialized) == (False, 1, 0)
    fake.connect("Headset")
    assert table.refresh() is True
    assert (table.initialized, fake.terminated, fake.initialized) == (True, 1, 1)
    assert fake.query_devices("output")["name"] == "Headset"


def test_refresh_is_a_no_op_before_sounddevice_is_imported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An import that has not happened will enumerate fresh; nothing is stale."""
    monkeypatch.delitem(sys.modules, "sounddevice", raising=False)
    assert DeviceTable().refresh() is False


def test_refresh_keeps_the_table_while_a_stream_is_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P1 of docs/device-table.tex: refresh never terminates PortAudio while a
    stream is reserved."""
    fake = FakeSoundDevice(input_name="Built-in", output_name="Built-in").install(
        monkeypatch
    )
    table = DeviceTable()
    reservation = table.reserve()
    fake.connect("Headset")
    assert table.refresh() is False
    assert fake.terminated == 0
    assert fake.query_devices("input")["name"] == "Built-in"
    reservation.release()
    assert table.refresh() is True
    assert fake.query_devices("input")["name"] == "Headset"


def test_open_streams_counts_reservations_up_and_down() -> None:
    table = DeviceTable()
    assert table.open_streams == 0
    first = table.reserve()
    second = table.reserve()
    assert (table.open_streams, first.held) == (2, True)
    first.release()
    assert (table.open_streams, first.held) == (1, False)
    second.release()
    assert table.open_streams == 0


def test_a_reservation_releases_once() -> None:
    table = DeviceTable()
    reservation = table.reserve()
    reservation.release()
    reservation.release()
    assert table.open_streams == 0


def test_releasing_more_than_was_reserved_is_a_bug() -> None:
    """P3 of docs/device-table.tex: reservations balance."""
    with pytest.raises(ValueError, match="no PortAudio stream is reserved"):
        DeviceTable().release()


def test_the_shared_table_is_one_object_per_process() -> None:
    assert DeviceTable.shared() is DeviceTable.shared()
    assert DeviceTable() is not DeviceTable.shared()


def test_refresh_reports_a_default_it_cannot_describe_and_still_re_reads(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The summary never fails the refresh, a default name it cannot decode included."""
    fake = FakeSoundDevice(input_name="Built-in", output_name="Built-in").install(
        monkeypatch
    )
    module = sys.modules["sounddevice"]

    def query_devices(kind: Kind | None = None) -> DeviceInfo | list[DeviceInfo]:
        if kind == "input":  # Describing the default input reaches the bad name.
            raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
        return fake.query_devices(kind) if kind else fake.query_devices()

    monkeypatch.setattr(module, "query_devices", query_devices)
    fake.connect("Headset")
    with caplog.at_level(logging.INFO):
        assert DeviceTable().refresh() is True
    assert fake.query_devices("output")["name"] == "Headset"
    (line,) = caplog.messages
    assert line == (
        "PortAudio device table re-read from the system: 2 devices, "
        "default input undescribable, default output #1"
    )
