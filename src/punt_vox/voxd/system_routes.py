"""Build the daemon's system routes: chime, voices, health and the mic probe.

The handler registry once imported each system handler and its chime
collaborators directly, which pushed its fan-out past the module coupling
limit. These four routes share nothing with synthesis or programs, so they
are assembled here and the registry imports one name.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, final

from punt_vox.voxd.chimes import ChimeResolver
from punt_vox.voxd.dedup import ChimeDedup
from punt_vox.voxd.mic_probe import MicProbeHandler
from punt_vox.voxd.system_handlers import ChimeHandler, HealthHandler, VoicesHandler

if TYPE_CHECKING:
    from punt_vox.voxd.health import DaemonHealth
    from punt_vox.voxd.playback import PlaybackQueue
    from punt_vox.voxd.types import MessageHandler

__all__ = ["SystemRoutes"]


@final
class SystemRoutes:
    """The ``{message type -> handler}`` slice that needs no subsystem wiring."""

    __slots__ = ()

    @staticmethod
    def build(
        *, playback: PlaybackQueue, health: DaemonHealth
    ) -> dict[str, MessageHandler]:
        """Return the chime, voices, health and mic probe routes."""
        return {
            "chime": ChimeHandler(
                chimes=ChimeResolver(),
                chime_dedup=ChimeDedup(),
                playback=playback,
            ),
            "voices": VoicesHandler(),
            "health": HealthHandler(health=health),
            "mic_probe": MicProbeHandler(),
        }
