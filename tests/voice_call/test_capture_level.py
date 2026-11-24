"""Tests for :mod:`punt_vox.voxd.voice_call.capture_level`."""

from __future__ import annotations

import logging
from array import array
from typing import TYPE_CHECKING

from punt_vox.voxd.voice_call.capture_level import CaptureLevel

if TYPE_CHECKING:
    import pytest

_LOGGER = "punt_vox.voxd.voice_call.capture_level"


def _lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.message for r in caplog.records if r.name == _LOGGER]


def test_the_level_is_logged_when_the_first_second_completes(
    caplog: pytest.LogCaptureFixture,
) -> None:
    meter = CaptureLevel()
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        meter.measure(array("h", [16384] * 8000), 0.0, 0.5)
        assert _lines(caplog) == []
        meter.measure(array("h", [0] * 8000), 0.5, 1.0)
    assert _lines(caplog) == [
        "Call capture level over the first second: peak 16384, rms 11585"
    ]


def test_blocks_after_the_first_second_are_ignored(
    caplog: pytest.LogCaptureFixture,
) -> None:
    meter = CaptureLevel()
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        meter.measure(array("h", [1] * 16000), 0.0, 1.0)
        meter.measure(array("h", [32767] * 16000), 1.0, 2.0)
    assert _lines(caplog) == ["Call capture level over the first second: peak 1, rms 1"]


def test_an_empty_first_second_logs_zeros(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        CaptureLevel().measure(array("h"), 0.0, 1.0)
    assert _lines(caplog) == ["Call capture level over the first second: peak 0, rms 0"]
