"""Builds the voxd WebSocket handler dispatch table from wired subsystems."""

from __future__ import annotations

from typing import TYPE_CHECKING, Self, final

from punt_vox.voxd.chimes import ChimeResolver
from punt_vox.voxd.dedup import ChimeDedup, OnceDedup
from punt_vox.voxd.record_handler import RecordHandler
from punt_vox.voxd.speech_handlers import SynthesizeHandler
from punt_vox.voxd.system_handlers import ChimeHandler, HealthHandler, VoicesHandler

if TYPE_CHECKING:
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
        self = super().__new__(cls)
        self._synthesis = synthesis
        self._playback = playback
        self._programs = programs
        self._health = health
        return self

    def build(self) -> dict[str, MessageHandler]:
        """Return the canonical handler dispatch dict (speech + system + programs)."""
        return {
            "synthesize": SynthesizeHandler(
                synthesis=self._synthesis,
                playback=self._playback,
                once_dedup=OnceDedup(),
            ),
            "record": RecordHandler(synthesis=self._synthesis),
            "chime": ChimeHandler(
                chimes=ChimeResolver(),
                chime_dedup=ChimeDedup(),
                playback=self._playback,
            ),
            "voices": VoicesHandler(),
            "health": HealthHandler(health=self._health),
            **self._programs.handlers(),
        }
