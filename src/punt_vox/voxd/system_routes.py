"""Build the daemon's system routes: chime, voices, health and the mic probe.

These four routes share nothing with synthesis or programs. Assembling them
here, with the chime collaborators they need, keeps the handler registry's
fan-out within the module coupling limit: it imports one name.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, final

from punt_vox.voxd.chimes import ChimeResolver
from punt_vox.voxd.dedup import ChimeDedup
from punt_vox.voxd.mic_probe import MicProbeHandler
from punt_vox.voxd.system_handlers import ChimeHandler, HealthHandler, VoicesHandler

if TYPE_CHECKING:
    from punt_vox.voxd.health import DaemonHealth
    from punt_vox.voxd.output_health import OutputHealth
    from punt_vox.voxd.playback import PlaybackQueue
    from punt_vox.voxd.types import MessageHandler

__all__ = ["SystemRoutes"]


@final
class SystemRoutes:
    """The ``{message type -> handler}`` slice that needs no subsystem wiring."""

    __slots__ = ()

    @staticmethod
    def build(
        *,
        playback: PlaybackQueue,
        health: DaemonHealth,
        output_health: OutputHealth | None = None,  # absent in a bare build
    ) -> dict[str, MessageHandler]:
        """Return the chime, voices, health and mic probe routes."""
        return {
            "chime": ChimeHandler(
                chimes=ChimeResolver(),
                chime_dedup=ChimeDedup(),
                playback=playback,
            ),
            "voices": VoicesHandler(),
            "health": HealthHandler(health=health, output_health=output_health),
            "mic_probe": MicProbeHandler(),
        }
