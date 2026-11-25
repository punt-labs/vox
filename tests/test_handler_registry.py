"""Tests for punt_vox.voxd.handler_registry -- the dispatch table's routes."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch

from punt_vox.voxd.handler_registry import HandlerRegistry
from punt_vox.voxd.health import DaemonHealth
from punt_vox.voxd.mic_probe import MicProbeHandler
from punt_vox.voxd.playback import PlaybackQueue
from punt_vox.voxd.programs.wiring import ProgramSubsystem
from punt_vox.voxd.synthesis import SynthesisPipeline
from punt_vox.voxd.workers.handlers import WorkerHandlers


def test_build_routes_the_mic_probe(tmp_path: Path) -> None:
    """The daemon answers ``mic_probe`` so doctor never captures locally."""
    playback = PlaybackQueue()
    programs = ProgramSubsystem(tmp_path / "programs", Mock(), tmp_path / "mpv.sock")
    with patch.object(WorkerHandlers, "handlers", Mock(return_value={})):
        routes = HandlerRegistry(
            synthesis=SynthesisPipeline(playback_mutex=playback.mutex),
            playback=playback,
            programs=programs,
            health=DaemonHealth(playback, lambda: 0, 0),
        ).build()

    assert isinstance(routes["mic_probe"], MicProbeHandler)
