"""voxd package -- audio server daemon."""
# pyright: reportPrivateUsage=false
# Re-exporting private names from submodules is the whole point of __init__.py.

from __future__ import annotations

from punt_vox.voxd.chimes import ChimeResolver
from punt_vox.voxd.config import (
    DaemonConfig,
    _config_dir,
    _install_token_redact_filter,
    _log_dir,
    _run_dir,
)
from punt_vox.voxd.daemon import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    VoxDaemon,
    build_app,
    cli,
    entrypoint,
    read_port_file,
    read_token_file,
)
from punt_vox.voxd.dedup import (
    _DEDUP_WINDOW_SECONDS,
    _ONCE_DEDUP_MAX_ENTRIES,
    _ONCE_DEDUP_MAX_TTL_SECONDS,
    ChimeDedup,
    DedupHit,
    OnceDedup,
)
from punt_vox.voxd.health import DaemonHealth
from punt_vox.voxd.music_handlers import (
    MusicListHandler,
    MusicNextHandler,
    MusicOffHandler,
    MusicOnHandler,
    MusicPlayHandler,
    MusicVibeHandler,
)
from punt_vox.voxd.music_scheduler import (
    _MUSIC_MAX_RETRIES,
    MusicScheduler,
    _PlaybackWaitResult,
)
from punt_vox.voxd.playback import (  # pyright: ignore[reportPrivateUsage]
    _MAX_STDERR_LEN,
    _PLAYBACK_TIMEOUT_DEFAULT_S,
    PlaybackItem,
    PlaybackQueue,
    _monotonic,
    _music_player_command,
    _probe_duration,
    _truncate_stderr,
)
from punt_vox.voxd.router import WebSocketRouter
from punt_vox.voxd.speech_handlers import RecordHandler, SynthesizeHandler
from punt_vox.voxd.synthesis import (  # pyright: ignore[reportPrivateUsage]
    _LOCAL_PROVIDERS,
    _PROVIDER_API_KEY_VAR,
    SynthesisPipeline,
    _build_audio_request,
    _run_play_directly_sync,
)
from punt_vox.voxd.system_handlers import ChimeHandler, HealthHandler, VoicesHandler
from punt_vox.voxd.track_generator import TrackGenerator
from punt_vox.voxd.types import MessageHandler

__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "_DEDUP_WINDOW_SECONDS",
    "_LOCAL_PROVIDERS",
    "_MAX_STDERR_LEN",
    "_MUSIC_MAX_RETRIES",
    "_ONCE_DEDUP_MAX_ENTRIES",
    "_ONCE_DEDUP_MAX_TTL_SECONDS",
    "_PLAYBACK_TIMEOUT_DEFAULT_S",
    "_PROVIDER_API_KEY_VAR",
    "ChimeDedup",
    "ChimeHandler",
    "ChimeResolver",
    "DaemonConfig",
    "DaemonHealth",
    "DedupHit",
    "HealthHandler",
    "MessageHandler",
    "MusicListHandler",
    "MusicNextHandler",
    "MusicOffHandler",
    "MusicOnHandler",
    "MusicPlayHandler",
    "MusicScheduler",
    "MusicVibeHandler",
    "OnceDedup",
    "PlaybackItem",
    "PlaybackQueue",
    "RecordHandler",
    "SynthesisPipeline",
    "SynthesizeHandler",
    "TrackGenerator",
    "VoicesHandler",
    "VoxDaemon",
    "WebSocketRouter",
    "_PlaybackWaitResult",
    "_build_audio_request",
    "_config_dir",
    "_install_token_redact_filter",
    "_log_dir",
    "_monotonic",
    "_music_player_command",
    "_probe_duration",
    "_run_dir",
    "_run_play_directly_sync",
    "_truncate_stderr",
    "build_app",
    "cli",
    "entrypoint",
    "read_port_file",
    "read_token_file",
]
