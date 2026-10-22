"""Tests for how the panel shows a provider's rejection of its key."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast, final

from punt_vox.client_errors import VoxdRejectionError
from punt_vox.config import VoxConfig
from punt_vox.panel.service import VoxPanelService
from punt_vox.types_provider_errors import ProviderAuthError

if TYPE_CHECKING:
    from punt_vox.panel.ports import PanelDaemonClient


@final
class _RejectingClient:
    """A daemon that refuses every roster fetch with *reason*."""

    def __init__(self, reason: str) -> None:
        self._reason = reason

    def voices(self, provider: str | None = None) -> list[str]:
        raise VoxdRejectionError(self._reason)

    def synthesize(self, *args: object, **kwargs: object) -> object:
        raise NotImplementedError


@final
class _Store:
    def read(self) -> VoxConfig:
        return VoxConfig(
            notify="y",
            speak="y",
            vibe_mode="auto",
            voice="roger",
            provider="elevenlabs",
            model=None,
            vibe=None,
            vibe_tags=None,
        )

    def write_field(self, key: str, value: str) -> None:
        raise NotImplementedError

    def write_fields(self, updates: dict[str, str]) -> None:
        raise NotImplementedError


def test_a_rejected_provider_key_shows_its_reason() -> None:
    """The sentence voxd sends for a 401 is what the panel shows.

    ``ProviderAuthError`` is that sentence; the panel carries it into the notice
    unchanged, so a bad key reads as a bad key rather than as a generic daemon
    failure.
    """
    reason = str(ProviderAuthError("elevenlabs", 401))
    client = cast("PanelDaemonClient", _RejectingClient(reason))
    service = VoxPanelService(client, _Store())

    service.refresh()

    message = service.scene().notice.message
    assert "rejected the credentials (HTTP 401)" in message
    assert "operation failed" not in message
