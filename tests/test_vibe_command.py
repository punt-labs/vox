"""Tests for the vibe->music orchestrator (src/punt_vox/vibe_command.py)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from _program_fakes import FakeProgramGateway

from punt_vox.client_errors import VoxdConnectionError
from punt_vox.server import SessionConfig
from punt_vox.types_programs.status import ProgramStatus
from punt_vox.vibe_command import VibeCommand

if TYPE_CHECKING:
    import pytest


def _playing() -> ProgramStatus:
    return ProgramStatus.radio(None, None)


class TestVibeCommand:
    """apply() persists the vibe and hints at music only while a Program plays."""

    def test_hint_when_playing_names_style(self, tmp_path: Path) -> None:
        session = SessionConfig()
        session.style = "flamenco"
        gateway = FakeProgramGateway(status=_playing())

        command = VibeCommand(session, gateway, tmp_path)
        result = json.loads(command.apply("relaxing", "[calm]", "manual"))

        assert result["music_hint"].startswith("Music is playing (style=flamenco)")
        assert result["music"] == {"playing": True, "style": "flamenco"}
        assert result["vibe"]["vibe"] == "relaxing"

    def test_no_hint_when_music_off(self, tmp_path: Path) -> None:
        gateway = FakeProgramGateway(status=ProgramStatus.idle())

        command = VibeCommand(SessionConfig(), gateway, tmp_path)
        result = json.loads(command.apply("relaxing", None, None))

        assert "music_hint" not in result
        assert "music" not in result

    def test_never_posts_a_switch_signal(self, tmp_path: Path) -> None:
        """Layering: the vibe path reads status only -- it never drives the Program."""
        session = SessionConfig()
        session.style = "techno"
        gateway = FakeProgramGateway(status=_playing())

        VibeCommand(session, gateway, tmp_path).apply("wired", None, None)

        assert gateway.verbs() == ["status"]

    def test_emits_vibe_set_trace(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        session = SessionConfig()
        session.style = "flamenco"
        gateway = FakeProgramGateway(status=_playing())

        with caplog.at_level(logging.INFO, logger="punt_vox.vibe_command"):
            VibeCommand(session, gateway, tmp_path).apply("relaxing", None, "manual")

        traces = [
            r.getMessage() for r in caplog.records if "[vibe-trace]" in r.getMessage()
        ]
        assert any(
            "vibe set" in m
            and "music_playing=true" in m
            and "style=flamenco" in m
            and "hint=emitted" in m
            for m in traces
        )

    def test_daemon_down_still_persists_no_hint(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A status read that raises fails safe: mood persists, no hint, no crash."""
        gateway = MagicMock()
        gateway.status.side_effect = VoxdConnectionError("not running")
        session = SessionConfig()

        with caplog.at_level(logging.WARNING, logger="punt_vox.vibe_command"):
            result = json.loads(
                VibeCommand(session, gateway, tmp_path).apply("calm", None, "manual")
            )

        assert result["vibe"]["vibe"] == "calm"
        assert "music_hint" not in result

    def test_invalid_mode_reports_error(self, tmp_path: Path) -> None:
        result = json.loads(
            VibeCommand(SessionConfig(), FakeProgramGateway(), tmp_path).apply(
                None, None, "sideways"
            )
        )
        assert "error" in result

    def test_empty_change_reports_error(self, tmp_path: Path) -> None:
        result = json.loads(
            VibeCommand(SessionConfig(), FakeProgramGateway(), tmp_path).apply(
                None, None, None
            )
        )
        assert "error" in result
