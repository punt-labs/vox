"""Resolve a panel voice preview against the latest saved settings."""

from __future__ import annotations

from typing import TYPE_CHECKING, final

from punt_vox.panel.panel_notice import PanelNotice
from punt_vox.session_spec import SessionSpec
from punt_vox.types_synthesis import SynthesisSpec
from punt_vox.types_synthesis_errors import (
    ModelNotAvailableError,
    ProviderNotConfiguredError,
)

if TYPE_CHECKING:
    import logging

    from punt_vox.panel.ports import SettingsStore

__all__ = ["PreviewRequest"]


@final
class PreviewRequest:
    """Translate settings validation into either a wire spec or panel guidance."""

    @staticmethod
    def resolve(
        store: SettingsStore, voice: str, logger: logging.Logger
    ) -> SynthesisSpec | PanelNotice:
        """Read the current settings and resolve a preview without contacting voxd."""
        try:
            return SessionSpec(store.read()).fill(SynthesisSpec(voice=voice))
        except ProviderNotConfiguredError:
            logger.warning("vox-panel: voice preview refused: no provider set")
            return PanelNotice(
                "⚠ pick a provider from the Provider menu to preview a voice"
            )
        except ModelNotAvailableError as exc:
            logger.warning("vox-panel: voice preview refused: %s", exc)
            return PanelNotice.voxd_rejected(str(exc))
