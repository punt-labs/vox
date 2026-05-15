"""voxd package -- audio server daemon."""
# pyright: reportPrivateUsage=false
# Re-exporting private names from submodules is the whole point of __init__.py.

from __future__ import annotations

from punt_vox.voxd.daemon import build_app
from punt_vox.voxd.health import DaemonHealth
from punt_vox.voxd.music.generator import TrackGenerator
from punt_vox.voxd.playback import (
    _PLAYBACK_TIMEOUT_DEFAULT_S,
    PlaybackQueue,
    _music_player_command,
)

__all__ = [
    "_PLAYBACK_TIMEOUT_DEFAULT_S",
    "DaemonHealth",
    "PlaybackQueue",
    "TrackGenerator",
    "_music_player_command",
    "build_app",
]
