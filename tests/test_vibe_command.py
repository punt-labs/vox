"""Tests for the vibe->music orchestrator (src/punt_vox/vibe_command.py)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from _program_fakes import FakeProgramGateway

from punt_vox.client_errors import VoxdConnectionError
from punt_vox.server import SessionConfig
from punt_vox.types_programs.control import CommandOutcome
from punt_vox.types_programs.status import ProgramStatus
from punt_vox.vibe_command import MusicPreference, VibeCommand

if TYPE_CHECKING:
    import pytest


def _playing() -> ProgramStatus:
    return ProgramStatus.radio(None, None)


def _redirect_trace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point ``VibeTraceLog.default()`` at a temp file and return its path.

    Both emitters resolve the durable ``<state>/logs/vibe-trace.log`` at
    construction, so the test reads exactly what a human greps at runtime.
    Patch before constructing the command whose ``__new__`` resolves the sink.
    """
    monkeypatch.setattr("punt_vox.vibe_trace.log_dir", lambda: tmp_path)
    return tmp_path / "vibe-trace.log"


def _lines(log: Path) -> list[str]:
    """Return the recorded ``[vibe-trace]`` lines, or [] when nothing was written."""
    if not log.exists():
        return []
    return log.read_text(encoding="utf-8").splitlines()


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

    def test_confirm_started_trace_names_persisted_style_when_omitted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # music(on) may omit style so the daemon keeps the playing genre. The
        # trace must name that EFFECTIVE, persisted style -- never "-" -- or the
        # re-pool proof would claim no genre is in effect when one plainly is.
        log = _redirect_trace(monkeypatch, tmp_path)
        pref = MusicPreference("flamenco")

        pref.confirm_started(CommandOutcome.ok(""), None, "relaxing", authored=True)

        lines = _lines(log)
        assert any("music on style=flamenco" in line for line in lines)
        assert all("style=-" not in line for line in lines)

    def test_confirm_started_trace_names_explicit_style(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        log = _redirect_trace(monkeypatch, tmp_path)
        pref = MusicPreference("flamenco")

        pref.confirm_started(CommandOutcome.ok(""), "techno", "wired", authored=False)

        assert any("music on style=techno" in line for line in _lines(log))


class TestVibeCommand:
    """apply() persists the vibe and hints at music only while a Program plays."""

    def test_hint_when_playing_names_style(self, tmp_path: Path) -> None:
        gateway = FakeProgramGateway(status=_playing())
        pref = MusicPreference("flamenco")

        command = VibeCommand(SessionConfig(), gateway, tmp_path, pref)
        result = json.loads(command.apply("relaxing", "[calm]", "manual"))

        assert result["music_hint"].startswith('Music is playing (style="flamenco")')
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
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        log = _redirect_trace(monkeypatch, tmp_path)
        gateway = FakeProgramGateway(status=_playing())
        pref = MusicPreference("flamenco")

        VibeCommand(SessionConfig(), gateway, tmp_path, pref).apply(
            "relaxing", None, "manual"
        )

        assert any(
            line.startswith("[vibe-trace] vibe set")
            and "music_playing=true" in line
            and "style=flamenco" in line
            and "hint_emitted=true" in line
            for line in _lines(log)
        )

    def test_trace_playing_unknown_style_is_playing_without_hint(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Music audibly playing but style unknown: playing=true, hint_emitted=false.

        The trace reports the raw audible gate independent of the known style, so
        a re-pool that can't fire (no genre to name) never reads as not-playing.
        """
        log = _redirect_trace(monkeypatch, tmp_path)
        gateway = FakeProgramGateway(status=_playing())
        pref = MusicPreference()  # no known style

        VibeCommand(SessionConfig(), gateway, tmp_path, pref).apply(
            "relaxing", None, "manual"
        )

        assert any(
            "vibe set" in line
            and "music_playing=true" in line
            and "hint_emitted=false" in line
            for line in _lines(log)
        )

    def test_trace_not_playing_reports_music_playing_false(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Status available and idle: the trace reads the audible gate as false."""
        log = _redirect_trace(monkeypatch, tmp_path)
        gateway = FakeProgramGateway(status=ProgramStatus.idle())

        VibeCommand(SessionConfig(), gateway, tmp_path, MusicPreference()).apply(
            "relaxing", None, "manual"
        )

        assert any(
            "vibe set" in line
            and "music_playing=false" in line
            and "hint_emitted=false" in line
            for line in _lines(log)
        )

    def test_trace_status_unavailable_reports_music_playing_unknown(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failed status read is UNKNOWN, not off: the trace must not claim false.

        Reporting music_playing=false when the daemon is unreachable would mask an
        outage as "music is off" in the grep proof -- the true state is unknown.
        """
        log = _redirect_trace(monkeypatch, tmp_path)
        gateway = MagicMock()
        gateway.status.side_effect = VoxdConnectionError("not running")

        VibeCommand(
            SessionConfig(), gateway, tmp_path, MusicPreference("flamenco")
        ).apply("relaxing", None, "manual")

        assert any(
            "vibe set" in line
            and "music_playing=unknown" in line
            and "hint_emitted=false" in line
            for line in _lines(log)
        )

    def test_daemon_down_still_persists_no_hint(self, tmp_path: Path) -> None:
        """A status read that raises fails safe: mood persists, no hint, no crash."""
        gateway = MagicMock()
        gateway.status.side_effect = VoxdConnectionError("not running")
        pref = MusicPreference("flamenco")

        result = json.loads(
            VibeCommand(SessionConfig(), gateway, tmp_path, pref).apply(
                "calm", None, "manual"
            )
        )

        assert result["vibe"]["vibe"] == "calm"
        assert "music_hint" not in result

    def test_daemon_down_trace_carries_exception_detail(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The status-unavailable trace names the underlying failure.

        A bare "status unavailable" line masks a daemon connectivity/protocol
        fault; carrying the exception text keeps the failure diagnosable in the
        durable log a human greps.
        """
        log = _redirect_trace(monkeypatch, tmp_path)
        gateway = MagicMock()
        gateway.status.side_effect = VoxdConnectionError("connection refused")

        VibeCommand(
            SessionConfig(), gateway, tmp_path, MusicPreference("flamenco")
        ).apply("calm", None, "manual")

        assert any(
            "status unavailable" in line and "connection refused" in line
            for line in _lines(log)
        )

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
