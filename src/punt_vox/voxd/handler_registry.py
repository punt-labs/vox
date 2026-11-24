"""Builds the voxd WebSocket handler dispatch table from wired subsystems."""

from __future__ import annotations

from typing import TYPE_CHECKING, Self, final

from punt_vox.cache import cache_clear, cache_status
from punt_vox.logging_config import apply_log_level
from punt_vox.voxd.cache_handlers import CacheClearHandler, CacheStatusHandler
from punt_vox.voxd.chimes import ChimeResolver
from punt_vox.voxd.dedup import ChimeDedup, OnceDedup
from punt_vox.voxd.log_level_handler import LogLevelHandler
from punt_vox.voxd.provider_status_handler import ProviderStatusHandler
from punt_vox.voxd.recording_handlers import RecordingHandlers
from punt_vox.voxd.speech_handlers import SynthesizeHandler
from punt_vox.voxd.system_handlers import ChimeHandler, HealthHandler, VoicesHandler
from punt_vox.voxd.workers.context import WorkerContext

if TYPE_CHECKING:
    from contextlib import ExitStack

    from punt_vox.voxd.health import DaemonHealth
    from punt_vox.voxd.playback import PlaybackQueue
    from punt_vox.voxd.programs.wiring import ProgramSubsystem
    from punt_vox.voxd.synthesis import SynthesisPipeline
    from punt_vox.voxd.types import MessageHandler

__all__ = ["HandlerRegistry"]


@final
class HandlerRegistry:
    """Assemble the ``{message type -> handler}`` dispatch table.

    Extracted from the daemon composition root so wiring the concrete handlers
    (and the dedup/chime collaborators they need) lives in one focused module
    rather than fanning every handler import into the daemon.
    """

    __slots__ = ("_health", "_playback", "_programs", "_synthesis")

    _synthesis: SynthesisPipeline
    _playback: PlaybackQueue
    _programs: ProgramSubsystem
    _health: DaemonHealth

    def __new__(
        cls,
        *,
        synthesis: SynthesisPipeline,
        playback: PlaybackQueue,
        programs: ProgramSubsystem,
        health: DaemonHealth,
    ) -> Self:
        """Create a handler registry."""
        self = super().__new__(cls)
        self._synthesis = synthesis
        self._playback = playback
        self._programs = programs
        self._health = health
        return self

    def build(
        self,
        lifetime: ExitStack
        | None = None,  # PY-TS-14: standalone builds lack a lifespan.
    ) -> dict[str, MessageHandler]:
        """Return the canonical handler dispatch table."""
        return {
            "synthesize": SynthesizeHandler(
                synthesis=self._synthesis,
                playback=self._playback,
                once_dedup=OnceDedup(),
            ),
            **RecordingHandlers.build(
                self._synthesis, self._playback, self._programs.library
            ),
            "cache_status": CacheStatusHandler(cache_status),
            "cache_clear": CacheClearHandler(cache_clear),
            "set_log_level": LogLevelHandler(apply_log_level),
            "chime": ChimeHandler(
                chimes=ChimeResolver(),
                chime_dedup=ChimeDedup(),
                playback=self._playback,
            ),
            "voices": VoicesHandler(),
            "health": HealthHandler(health=self._health),
            "provider_status": ProviderStatusHandler(env_lock=self._synthesis.env_lock),
            **WorkerContext.handlers(
                self._playback,
                lifetime,
                hold_music=self._programs.service.hold_music_for_call,
            ),
            **self._programs.handlers(),
        }
