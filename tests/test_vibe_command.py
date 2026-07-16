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
from punt_vox.vibe_command import MusicPreference, VibeCommand

if TYPE_CHECKING:
    import pytest


def _playing() -> ProgramStatus:
    return ProgramStatus.radio(None, None)


class TestMusicPreference:
    """The style register the music tools keep current for the re-pool hint."""

    def test_defaults_to_no_style(self) -> None:
        assert MusicPreference().style is None

    def test_started_adopts_named_style(self) -> None:
        pref = MusicPreference()
        pref.started("flamenco")
        assert pref.style == "flamenco"

    def test_started_without_style_keeps_current(self) -> None:
        pref = MusicPreference("flamenco")
        pref.started(None)
        assert pref.style == "flamenco"

    def test_selected_adopts_style(self) -> None:
        pref = MusicPreference("flamenco")
        pref.selected("techno")
        assert pref.style == "techno"

    def test_selected_without_style_clears(self) -> None:
        pref = MusicPreference("flamenco")
        pref.selected(None)
        assert pref.style is None

    def test_stopped_clears(self) -> None:
        pref = MusicPreference("flamenco")
        pref.stopped()
        assert pref.style is None


class TestVibeCommand:
    """apply() persists the vibe and hints at music only while a Program plays."""

    def test_hint_when_playing_names_style(self, tmp_path: Path) -> None:
        gateway = FakeProgramGateway(status=_playing())
        pref = MusicPreference("flamenco")

        command = VibeCommand(SessionConfig(), gateway, tmp_path, pref)
        result = json.loads(command.apply("relaxing", "[calm]", "manual"))

        assert result["music_hint"].startswith("Music is playing (style=flamenco)")
        assert result["music"] == {"playing": True, "style": "flamenco"}
        assert result["vibe"]["vibe"] == "relaxing"

    def test_no_hint_when_music_off(self, tmp_path: Path) -> None:
        gateway = FakeProgramGateway(status=ProgramStatus.idle())

        command = VibeCommand(SessionConfig(), gateway, tmp_path, MusicPreference())
        result = json.loads(command.apply("relaxing", None, None))

        assert "music_hint" not in result
        assert "music" not in result

    def test_never_posts_a_switch_signal(self, tmp_path: Path) -> None:
        """Layering: the vibe path reads status only -- it never drives the Program."""
        gateway = FakeProgramGateway(status=_playing())

        pref = MusicPreference("techno")
        VibeCommand(SessionConfig(), gateway, tmp_path, pref).apply("wired", None, None)

        assert gateway.verbs() == ["status"]

    def test_emits_vibe_set_trace(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        gateway = FakeProgramGateway(status=_playing())
        pref = MusicPreference("flamenco")

        with caplog.at_level(logging.INFO, logger="punt_vox.vibe_command"):
            VibeCommand(SessionConfig(), gateway, tmp_path, pref).apply(
                "relaxing", None, "manual"
            )

        traces = [
            r.getMessage() for r in caplog.records if "[vibe-trace]" in r.getMessage()
        ]
        assert any(
            "vibe set" in m
            and "music_playing=true" in m
            and "style=flamenco" in m
            and "hint_emitted=true" in m
            for m in traces
        )

    def test_trace_playing_unknown_style_is_playing_without_hint(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Music audibly playing but style unknown: playing=true, hint_emitted=false.

        The trace reports the raw audible gate independent of the known style, so
        a re-pool that can't fire (no genre to name) never reads as not-playing.
        """
        gateway = FakeProgramGateway(status=_playing())
        pref = MusicPreference()  # no known style

        with caplog.at_level(logging.INFO, logger="punt_vox.vibe_command"):
            VibeCommand(SessionConfig(), gateway, tmp_path, pref).apply(
                "relaxing", None, "manual"
            )

        traces = [
            r.getMessage() for r in caplog.records if "[vibe-trace]" in r.getMessage()
        ]
        assert any(
            "vibe set" in m and "music_playing=true" in m and "hint_emitted=false" in m
            for m in traces
        )

    def test_daemon_down_still_persists_no_hint(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A status read that raises fails safe: mood persists, no hint, no crash."""
        gateway = MagicMock()
        gateway.status.side_effect = VoxdConnectionError("not running")
        pref = MusicPreference("flamenco")

        with caplog.at_level(logging.WARNING, logger="punt_vox.vibe_command"):
            result = json.loads(
                VibeCommand(SessionConfig(), gateway, tmp_path, pref).apply(
                    "calm", None, "manual"
                )
            )

        assert result["vibe"]["vibe"] == "calm"
        assert "music_hint" not in result

    def test_invalid_mode_reports_error(self, tmp_path: Path) -> None:
        command = VibeCommand(
            SessionConfig(), FakeProgramGateway(), tmp_path, MusicPreference()
        )
        assert "error" in json.loads(command.apply(None, None, "sideways"))

    def test_empty_change_reports_error(self, tmp_path: Path) -> None:
        command = VibeCommand(
            SessionConfig(), FakeProgramGateway(), tmp_path, MusicPreference()
        )
        assert "error" in json.loads(command.apply(None, None, None))
