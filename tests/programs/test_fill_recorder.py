"""Tests for FillRecorder's symmetric success/failure logging (§4 gap)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Self, cast, final

from punt_vox.voxd.programs import Program, ProgramState
from punt_vox.voxd.programs.control_channel import ControlChannel
from punt_vox.voxd.programs.fill_recorder import FillRecorder

from .conftest import AvoidRepeatPolicy

if TYPE_CHECKING:
    import pytest

    from punt_vox.voxd.programs.manifest import PartEntry
    from punt_vox.voxd.programs.store import PartStore


@final
class _RecordingStore:
    """A minimal PartStore that only captures recorded entries."""

    __slots__ = ("_recorded",)
    _recorded: list[PartEntry]

    def __new__(cls) -> Self:
        self = super().__new__(cls)
        self._recorded = []
        return self

    def record(self, entry: PartEntry) -> None:
        self._recorded.append(entry)

    @property
    def recorded(self) -> list[PartEntry]:
        return self._recorded


def _recorder() -> FillRecorder:
    channel = ControlChannel(Program(ProgramState.initial(), AvoidRepeatPolicy()))
    return FillRecorder(channel)


class TestFillRecorderLogging:
    """Generation success logs INFO, symmetric with the failure paths' WARNING."""

    def test_ready_logs_success_symmetrically(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        store = cast("PartStore", _RecordingStore())
        with caplog.at_level(
            logging.INFO, logger="punt_vox.voxd.programs.fill_recorder"
        ):
            _recorder().ready(store, 3, Path("part3.mp3"))
        infos = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
        assert infos == ["music: generated part 3"]

    def test_permanent_failure_logs_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        store = cast("PartStore", _RecordingStore())
        with caplog.at_level(
            logging.WARNING, logger="punt_vox.voxd.programs.fill_recorder"
        ):
            _recorder().permanent(store, 4, Path("part4.mp3"), ValueError("bad_prompt"))
        warnings = [
            r.getMessage() for r in caplog.records if r.levelno == logging.WARNING
        ]
        assert warnings == ["music: part 4 failed permanently: bad_prompt"]

    def test_transient_failure_is_debug_not_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(
            logging.DEBUG, logger="punt_vox.voxd.programs.fill_recorder"
        ):
            _recorder().transient(ValueError("rate limited"))
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert any(r.levelno == logging.DEBUG for r in caplog.records)
