"""``VoicePreview`` -- play a voice back through voxd and say what happened.

The panel's preview button is the one control that writes nothing: it builds a
wire spec from the settings the store holds, asks voxd to speak a fixed line,
and reports the outcome as the notice the panel should show. Owning none of
the panel's held state, it needs none of the panel's lock; the service decides
what to do with the notice it is handed.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Self, final

from punt_vox.client_errors import VoxdConnectionError
from punt_vox.panel.panel_notice import PanelNotice
from punt_vox.session_spec import SessionSpec
from punt_vox.types_synthesis import SynthesisSpec
from punt_vox.types_synthesis_errors import (
    ModelNotAvailableError,
    ProviderNotConfiguredError,
)

if TYPE_CHECKING:
    from punt_vox.panel.ports import PanelDaemonClient, SettingsStore

logger = logging.getLogger(__name__)

__all__ = ["VoicePreview"]

_PREVIEW_TEXT = "This is my voice."


@final
class VoicePreview:
    """Speak the preview line in one voice; answer the notice the outcome earns."""

    _client: PanelDaemonClient
    _store: SettingsStore
    __slots__ = ("_client", "_store")

    def __new__(cls, client: PanelDaemonClient, store: SettingsStore) -> Self:
        self = super().__new__(cls)
        self._client = client
        self._store = store
        return self

    def play(self, voice: str) -> PanelNotice:
        """Play *voice* back; a silent notice means the caller just heard it.

        Builds the wire spec through :class:`SessionSpec` off a fresh read of
        the config store, so the preview sends the provider state declares
        rather than letting voxd guess. An unconfigured or misconfigured state
        answers :meth:`PanelNotice.voxd_rejected`; a voxd that cannot be
        reached answers :meth:`PanelNotice.voxd_unavailable`. Anything else is
        a bug and propagates.
        """
        try:
            spec = SessionSpec(self._store.read()).fill(SynthesisSpec(voice=voice))
        except (ProviderNotConfiguredError, ModelNotAvailableError) as exc:
            logger.warning("vox-panel: voice preview refused: %s", exc)
            return PanelNotice.voxd_rejected(str(exc))
        try:
            self._client.synthesize(_PREVIEW_TEXT, spec)
        except VoxdConnectionError:
            logger.warning("vox-panel: voice preview failed -- voxd is not reachable")
            return PanelNotice.voxd_unavailable()
        return PanelNotice.silent()
