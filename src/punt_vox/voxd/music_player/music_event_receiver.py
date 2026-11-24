"""Decode and apply music events without putting private values in diagnostics."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Self, final

from punt_vox.voxd.music_player.lux_fault_boundary import LuxFaultBoundary
from punt_vox.voxd.music_player.player_event_codec import (
    AnchorUnresolvedError,
    PlayerEventCodec,
)
from punt_vox.voxd.music_player.wire import MusicTopic

if TYPE_CHECKING:
    from collections.abc import Mapping

    from punt_vox.voxd.music_player.command_ports import ProgramSeam
    from punt_vox.voxd.music_player.player_events import PlayerEvent
    from punt_vox.voxd.music_player.presenter_ports import ScenePresenter

logger = logging.getLogger(__name__)


@final
class MusicEventReceiver:
    """Own the event boundary and its client-visible failure presentation."""

    __slots__ = ("_codec", "_presenter", "_service")
    _codec: PlayerEventCodec
    _presenter: ScenePresenter
    _service: ProgramSeam

    def __new__(cls, service: ProgramSeam, presenter: ScenePresenter) -> Self:
        """Bind playback and scene ports for inbound events."""
        self = super().__new__(cls)
        self._service = service
        self._presenter = presenter
        self._codec = PlayerEventCodec()
        return self

    def receive(self, topic: str, payload: Mapping[str, object]) -> None:
        """Decode and apply once, surfacing unresolved anchors without logging them."""
        operation = topic if topic in MusicTopic else "unknown"
        logger.info("[lux] received %s; applying", operation)
        with LuxFaultBoundary(logger, "dropping music event: reason=unexpected_error"):
            albums = self._service.catalog_albums()
            try:
                event = self._codec.decode(topic, payload, albums)
            except AnchorUnresolvedError as exc:
                logger.warning(
                    "[lux] anchor on %s names no catalogued album; surfacing", operation
                )
                self._surface_resolve_failure(exc.anchor)
                return
            except ValueError:  # malformed frame or unknown topic: a rejection
                logger.warning("[lux] dropping music event: reason=invalid_event")
                return
            self._apply(event)

    def _apply(self, event: PlayerEvent) -> None:
        """Apply an event and refresh the scene on a refusal or fault."""
        refused = False
        with LuxFaultBoundary(
            logger, "music operation could not play; showing it in the scene"
        ) as fault:
            refused = event.apply(self._service) is False
        if refused:
            logger.info("[lux] ignoring transport control: Nothing playing.")
        if fault.failed() or refused:
            self._surface_failure(event)

    def _surface_failure(self, event: PlayerEvent) -> None:
        """Present a refused operation without leaking exception details."""
        with LuxFaultBoundary(logger, "could not surface the playback failure"):
            event.surface_failure(self._presenter)

    def _surface_resolve_failure(self, anchor: str) -> None:
        """Present a stale selection without leaking it through exception chains."""
        with LuxFaultBoundary(logger, "could not surface the resolve failure"):
            self._presenter.present_resolve_failure(anchor)
