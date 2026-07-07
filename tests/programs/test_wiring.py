"""Tests for the ProgramSubsystem facade -- the daemon's one Programs seam."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

from punt_vox.voxd.programs.service import ProgramService
from punt_vox.voxd.programs.wiring import ProgramSubsystem

from .conftest import QuietProducer, seed_program

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _subsystem(root: Path) -> ProgramSubsystem:
    """Build a subsystem with a fake producer (Fix #5e: producer is injectable)."""
    return ProgramSubsystem(root, QuietProducer())


_EXPECTED_HANDLERS = frozenset(
    {
        "program_on",
        "program_off",
        "program_next",
        "program_play",
        "program_loop",
        "program_list",
        "program_status",
    }
)


class TestHandlerRoster:
    """handlers() exposes exactly the seven program_* wire adapters."""

    def test_exactly_the_seven_program_handlers(self, tmp_path: Path) -> None:
        subsystem = _subsystem(tmp_path / "programs")
        assert set(subsystem.handlers()) == _EXPECTED_HANDLERS

    def test_service_is_a_program_service(self, tmp_path: Path) -> None:
        subsystem = _subsystem(tmp_path / "programs")
        assert isinstance(subsystem.service, ProgramService)


class TestHandlersBoundToService:
    """The handlers read and drive the subsystem's own service, live."""

    def test_status_handler_reports_idle_before_any_command(
        self, tmp_path: Path
    ) -> None:
        subsystem = _subsystem(tmp_path / "programs")
        ws = MagicMock()
        ws.send_json = AsyncMock()

        asyncio.run(subsystem.handlers()["program_status"]({"id": "s1"}, ws))

        reply = ws.send_json.await_args.args[0]
        assert reply["type"] == "program_status"
        assert reply["status"]["mode"] == "off"

    def test_list_handler_reports_saved_programs(self, tmp_path: Path) -> None:
        root = tmp_path / "programs"
        seed_program(root, "ambient_calm", 1, 2)
        subsystem = _subsystem(root)
        ws = MagicMock()
        ws.send_json = AsyncMock()

        asyncio.run(subsystem.handlers()["program_list"]({"id": "l1"}, ws))

        reply = ws.send_json.await_args.args[0]
        assert reply["type"] == "program_list"
        assert [p["name"] for p in reply["programs"]] == ["ambient_calm"]


class TestLegacyMigrationGate:
    """legacy_migration_pending gates the daemon's one-line migrate hint."""

    def test_true_when_legacy_tracks_exist_and_no_programs(
        self, tmp_path: Path
    ) -> None:
        legacy = tmp_path / "tracks"
        legacy.mkdir()
        (legacy / "ambient_calm_20260101_1200_1.mp3").write_bytes(b"audio")
        subsystem = _subsystem(tmp_path / "programs")
        assert subsystem.legacy_migration_pending(legacy) is True

    def test_false_when_a_program_already_exists(self, tmp_path: Path) -> None:
        root = tmp_path / "programs"
        seed_program(root, "ambient_calm", 1)
        legacy = tmp_path / "tracks"
        legacy.mkdir()
        (legacy / "ambient_calm_20260101_1200_1.mp3").write_bytes(b"audio")
        subsystem = _subsystem(root)
        assert subsystem.legacy_migration_pending(legacy) is False

    def test_false_when_no_legacy_tracks(self, tmp_path: Path) -> None:
        subsystem = _subsystem(tmp_path / "programs")
        assert subsystem.legacy_migration_pending(tmp_path / "tracks") is False

    def test_log_legacy_hint_fires_once_when_pending(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        legacy = tmp_path / "tracks"
        legacy.mkdir()
        (legacy / "ambient_calm_20260101_1200_1.mp3").write_bytes(b"audio")
        subsystem = _subsystem(tmp_path / "programs")
        with caplog.at_level(logging.INFO, logger="punt_vox.voxd.programs.wiring"):
            subsystem.log_legacy_hint(legacy)
        assert sum("vox music migrate" in r.getMessage() for r in caplog.records) == 1

    def test_log_legacy_hint_silent_when_nothing_to_migrate(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        subsystem = _subsystem(tmp_path / "programs")
        with caplog.at_level(logging.INFO, logger="punt_vox.voxd.programs.wiring"):
            subsystem.log_legacy_hint(tmp_path / "tracks")
        assert not caplog.records
