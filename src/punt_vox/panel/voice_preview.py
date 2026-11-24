"""``VoicePreview`` -- play a voice back through voxd and say what happened.

The panel's preview button is the one control that writes nothing: it builds a
wire spec from the settings the store holds, asks voxd to speak a fixed line,
and reports the outcome as the notice the panel should show. Owning none of
the panel's held state, it needs none of the panel's lock; the service decides
what to do with the notice it is handed.

The logger is the service's, injected the way
:class:`~punt_vox.panel.menu_entry.PanelMenuEntry` takes the leg's: a refused
or unheard preview is one of the panel's controls failing, and its lines belong
under the same name as every other control's.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Self, final

from punt_vox.client_errors import VoxdConnectionError
from punt_vox.panel.panel_notice import PanelNotice
from punt_vox.panel.preview_request import PreviewRequest

if TYPE_CHECKING:
    import logging

    from punt_vox.panel.ports import PanelDaemonClient, SettingsStore

__all__ = ["VoicePreview"]

_PREVIEW_TEXT = "This is my voice."


@final
class VoicePreview:
    """Speak the preview line in one voice; answer the notice the outcome earns."""

    _client: PanelDaemonClient
    _store: SettingsStore
    _logger: logging.Logger
    __slots__ = ("_client", "_logger", "_store")

    def __new__(
        cls, client: PanelDaemonClient, store: SettingsStore, logger: logging.Logger
    ) -> Self:
        """Create a voice preview."""
        self = super().__new__(cls)
        self._client = client
        self._store = store
        self._logger = logger
        return self

    def play(self, voice: str) -> PanelNotice:
        """Play *voice* back; a silent notice means the caller just heard it.

        A :class:`PreviewRequest` resolves the wire spec from the current
        settings or returns a notice. Only a resolved request reaches voxd.
        """
        request = PreviewRequest.resolve(self._store, voice, self._logger)
        if isinstance(request, PanelNotice):
            return request
        try:
            self._client.synthesize(_PREVIEW_TEXT, request)
        except VoxdConnectionError:
            self._logger.warning(
                "vox-panel: voice preview failed -- voxd is not reachable"
            )
            return PanelNotice.voxd_unavailable()
        return PanelNotice.silent()
