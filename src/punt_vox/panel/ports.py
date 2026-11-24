"""The structural seams the panel package depends on.

``SettingsSource`` and ``RosterClient`` are the two reads
:class:`~punt_vox.panel.state.PanelState` needs -- the config store and the
daemon's voice list. Each lets a test drive its dependent with an in-memory
fake; the concrete ``ConfigStore`` and ``VoxClientSync`` satisfy them
structurally.

The panel's REST client is the ``punt_lux.LuxClient`` facade itself, held
directly rather than fronted by a panel-local protocol -- the facade's
noun-grouped accessors (``client.scene``, ``client.callback``) are the surface,
and shimming them behind a second name would only add drift.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from punt_vox.voice_target import RosterClient

if TYPE_CHECKING:
    from collections.abc import Callable

    from punt_vox.client import SynthesizeResult
    from punt_vox.config import VoxConfig
    from punt_vox.types_synthesis import SynthesisSpec

__all__ = [
    "HubListener",
    "PanelDaemonClient",
    "SettingsSource",
    "SettingsStore",
]


@runtime_checkable
class HubListener(Protocol):
    """The one live hub connection: subscribe topics, then listen with reconnect."""

    def subscribe(self, *topics: str) -> None:
        """Record the topics to (re)subscribe on every connect; call before listen."""
        ...

    async def listen(self) -> None:
        """Hold the connection open, dispatching frames, until it is stopped."""
        ...


@runtime_checkable
class SettingsSource(Protocol):
    """The config-store read :class:`~punt_vox.panel.state.PanelState` needs."""

    def read(self) -> VoxConfig:
        """Return every config field, merging the durable and ephemeral files."""
        ...


@runtime_checkable
class SettingsStore(SettingsSource, Protocol):
    """The config-store read and write ``VoxPanelService`` needs."""

    def write_fields(
        self,
        updates: dict[str, str],
        *,
        check: Callable[[dict[str, str]], None] | None = None,
    ) -> None:
        """Write fields atomically, checking the current file when requested."""
        ...


@runtime_checkable
class PanelDaemonClient(RosterClient, Protocol):
    """The daemon reads and the preview write ``VoxPanelService`` needs.

    Extends :class:`RosterClient` (the roster read) with the one RPC the
    ▶ preview button uses to play a candidate voice back without committing
    it to config.
    """

    def synthesize(
        self, text: str, spec: SynthesisSpec | None = None, *, once: int | None = None
    ) -> SynthesizeResult:
        """Send a synthesize request; audio plays on the daemon host."""
        ...
