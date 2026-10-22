"""Contract: vox's menu entries reach lux carrying the frame they reopen.

A menu click reopens a frame the user closed only if the entry was registered
with that frame's id; a content push never does it. These tests run vox's two
registration paths through lux's real ``CallbackAccessor`` and its real request
validation, standing in only for the HTTP call, so a lux that stopped accepting
a ``frame_id`` -- or a vox that stopped sending one -- fails here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast, final

from panel.doubles import PANEL_LOGGER
from punt_lux import LuxClient, OpError
from punt_lux.client.callback import CallbackAccessor
from punt_lux.operations import Ok
from punt_lux.operations.models.callback_fields import CallbackFields
from punt_lux.operations.models.callbacks import RegisterCallbackRequest

from punt_vox.lux_common import MenuEntry
from punt_vox.panel.menu_entry import PanelMenuEntry
from punt_vox.panel.panel_guard import PanelGuard
from punt_vox.panel.service import VoxPanelService
from punt_vox.voxd.music_player.lux_menu import LuxMenuRegistrar
from punt_vox.voxd.music_player.player import MusicPlayer

if TYPE_CHECKING:
    from punt_lux.client._rest_transport import _RestTransport
    from punt_lux.domain.hub.client_identity import ClientIdentity

    from punt_vox.panel.ports import PanelDaemonClient, SettingsStore


@final
class _ValidatingRest:
    """Runs lux's own request validation, records what would be posted."""

    def __init__(self) -> None:
        self.posted: list[RegisterCallbackRequest] = []

    def register_callback(
        self, callback_id: str, label: str, frame_id: str | None = None
    ) -> Ok | OpError:
        request = RegisterCallbackRequest.parse(
            CallbackFields(callback_id, label, frame_id)
        )
        if isinstance(request, OpError):
            return request
        self.posted.append(request)
        return Ok()


@final
class _LuxClientOverRest:
    def __init__(self, rest: _ValidatingRest) -> None:
        self.callback = CallbackAccessor(
            cast("_RestTransport", rest), cast("ClientIdentity", None)
        )


async def test_the_music_entry_reaches_lux_with_the_music_frame() -> None:
    rest = _ValidatingRest()
    client = cast("LuxClient", _LuxClientOverRest(rest))
    entry = MenuEntry("music", "Music", MusicPlayer.frame_id())

    await LuxMenuRegistrar(lambda: client).register(entry)

    assert [r.callback.frame_id for r in rest.posted] == ["vox.music"]


async def test_the_panel_entry_reaches_lux_with_the_panel_frame() -> None:
    rest = _ValidatingRest()
    client = cast("LuxClient", _LuxClientOverRest(rest))
    # Nothing is read from the daemon or the store to register the entry.
    service = VoxPanelService(
        cast("PanelDaemonClient", object()), cast("SettingsStore", object())
    )
    guard = PanelGuard(service, lambda: client, PANEL_LOGGER)
    entry = PanelMenuEntry(service, lambda: client, guard, PANEL_LOGGER)

    assert await entry.registered() is True
    assert [r.callback.frame_id for r in rest.posted] == ["vox.panel"]
