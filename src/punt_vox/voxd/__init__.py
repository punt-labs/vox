"""voxd package -- audio server daemon."""
# pyright: reportPrivateUsage=false
# Re-exporting private names from submodules is the whole point of __init__.py.

from __future__ import annotations

from punt_vox.voxd._monolith import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    DaemonContext,
    _apply_vibe_for_synthesis,
    _auto_track_name,
    _handle_music_on,
    _health_payload_full,
    _health_payload_minimal,
    _health_route,
    _kill_music_proc,
    _load_keys,
    _model_supports_expressive_tags,
    _music_loop,
    _synthesize_to_file,
    _try_direct_play,
    build_app,
    cli,
    entrypoint,
    main,
    read_port_file,
    read_token_file,
)
from punt_vox.voxd.chimes import ChimeResolver
from punt_vox.voxd.config import (
    DaemonConfig,
    _config_dir,
    _install_token_redact_filter,
    _log_dir,
    _run_dir,
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
from punt_vox.voxd.synthesis import (  # pyright: ignore[reportPrivateUsage]
    _LOCAL_PROVIDERS,
    _PROVIDER_API_KEY_VAR,
    SynthesisPipeline,
    _build_audio_request,
    _run_play_directly_sync,
)
from punt_vox.voxd.track_generator import TrackGenerator

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
    "ChimeResolver",
    "DaemonConfig",
    "DaemonContext",
    "DaemonHealth",
    "DedupHit",
    "MusicScheduler",
    "OnceDedup",
    "PlaybackItem",
    "PlaybackQueue",
    "SynthesisPipeline",
    "TrackGenerator",
    "WebSocketRouter",
    "_PlaybackWaitResult",
    "_apply_vibe_for_synthesis",
    "_auto_track_name",
    "_build_audio_request",
    "_config_dir",
    "_handle_music_on",
    "_health_payload_full",
    "_health_payload_minimal",
    "_health_route",
    "_install_token_redact_filter",
    "_kill_music_proc",
    "_load_keys",
    "_log_dir",
    "_model_supports_expressive_tags",
    "_monotonic",
    "_music_loop",
    "_music_player_command",
    "_probe_duration",
    "_run_dir",
    "_run_play_directly_sync",
    "_synthesize_to_file",
    "_truncate_stderr",
    "_try_direct_play",
    "build_app",
    "cli",
    "entrypoint",
    "main",
    "read_port_file",
    "read_token_file",
]
