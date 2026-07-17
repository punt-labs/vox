"""Tests for the auto-vibe nudge hook (src/punt_vox/nudge_hook.py)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

from punt_vox.config import ConfigStore, VoxConfig
from punt_vox.nudge_hook import NudgeHook
from punt_vox.vibe_nudge import DEFAULT_THRESHOLD, VIBE_NUDGE_REMINDER

if TYPE_CHECKING:
    import pytest


def _redirect_trace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point the default sink at a temp file and return its path.

    ``NudgeHook`` resolves ``VibeTraceLog.default()`` -- the durable
    ``<state>/logs/vibe-trace.log`` -- so the test reads exactly what a human
    greps at runtime instead of a log record the host would discard.
    """
    monkeypatch.setattr("punt_vox.vibe_trace.log_dir", lambda: tmp_path)
    return tmp_path / "vibe-trace.log"


def _config(*, mode: str, turns: int) -> VoxConfig:
    return VoxConfig(
        notify="y",
        speak="y",
        vibe_mode=mode,
        voice=None,
        provider=None,
        model=None,
        vibe=None,
        vibe_tags=None,
        vibe_nudge_turns=turns,
    )


class TestNudgeHook:
    """The nudge fires on the Nth auto prompt and emits the [vibe-trace] event."""

    def test_fires_and_traces_on_threshold(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        log = _redirect_trace(monkeypatch, tmp_path)
        config = _config(mode="auto", turns=DEFAULT_THRESHOLD - 1)
        result = NudgeHook(tmp_path).run(config)
        assert result is not None
        envelope = result["hookSpecificOutput"]
        assert isinstance(envelope, dict)
        assert envelope["additionalContext"] == VIBE_NUDGE_REMINDER
        assert ConfigStore(tmp_path).read().vibe_nudge_turns == 0
        lines = log.read_text(encoding="utf-8").splitlines()
        assert any(
            line.startswith("[vibe-trace] nudge fired") and "mode=auto" in line
            for line in lines
        )

    def test_silent_below_threshold_emits_no_trace(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        log = _redirect_trace(monkeypatch, tmp_path)
        config = _config(mode="auto", turns=0)
        assert NudgeHook(tmp_path).run(config) is None
        assert not log.exists()
        assert ConfigStore(tmp_path).read().vibe_nudge_turns == 1

    def test_manual_mode_stays_silent(self, tmp_path: Path) -> None:
        assert NudgeHook(tmp_path).run(_config(mode="manual", turns=99)) is None

    def test_persist_failure_stays_silent(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        config = _config(mode="auto", turns=DEFAULT_THRESHOLD - 1)
        with (
            patch.object(ConfigStore, "write_field", side_effect=OSError("read-only")),
            caplog.at_level(logging.WARNING, logger="punt_vox.nudge_hook"),
        ):
            assert NudgeHook(tmp_path).run(config) is None
        assert any("vibe-nudge" in r.getMessage() for r in caplog.records)
