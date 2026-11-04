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
from punt_lux.domain.hub.menu_frames import MenuFrames
from punt_lux.domain.ids import ConnectionId
from punt_lux.operations import Ok
from punt_lux.operations.models.callback_fields import CallbackFields
from punt_lux.operations.models.callbacks import RegisterCallbackRequest

from punt_vox.lux_common import MenuEntry
from punt_vox.panel.menu_entry import PanelMenuEntry
from punt_vox.panel.panel_guard import PanelGuard
from punt_vox.panel.service import VoxPanelService
from punt_vox.voxd.music_player.album_roster import AlbumRoster
from punt_vox.voxd.music_player.lux_menu import LuxMenuRegistrar
from punt_vox.voxd.music_player.player import MusicPlayer
from punt_vox.voxd.music_player.player_view import PlayerView
from punt_vox.voxd.music_player.scene import AlbumListScene
from punt_vox.voxd.music_player.track_count_cache import TrackCountCache

if TYPE_CHECKING:
    from punt_lux import RenderRequest
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


@final
class _ScopedFrames:
    """The Hub's store keys for one connection: the menu's frame, the scene's."""

    def __init__(self, connection: str) -> None:
        self._connection = ConnectionId(connection)

    def of_entry(self, request: RegisterCallbackRequest) -> str | None:
        # None: the entry named no frame, which is the defect under test.
        return MenuFrames(self._connection).of(request.callback)

    def of_scene(self, request: RenderRequest) -> str:
        return request.presentation().scoped(self._connection).frame_id


async def _registered_music_entry() -> list[RegisterCallbackRequest]:
    rest = _ValidatingRest()
    client = cast("LuxClient", _LuxClientOverRest(rest))
    entry = MenuEntry("music", "Music", MusicPlayer.frame_id())

    await LuxMenuRegistrar(lambda: client).register(entry)
    return rest.posted


def _panel_service() -> VoxPanelService:
    # Nothing is read from the daemon or the store to register the entry.
    return VoxPanelService(
        cast("PanelDaemonClient", object()), cast("SettingsStore", object())
    )


async def _registered_panel_entry(
    service: VoxPanelService,
) -> list[RegisterCallbackRequest]:
    rest = _ValidatingRest()
    client = cast("LuxClient", _LuxClientOverRest(rest))
    guard = PanelGuard(service, lambda: client, PANEL_LOGGER)
    entry = PanelMenuEntry(service, lambda: client, guard, PANEL_LOGGER)

    assert await entry.registered() is True
    return rest.posted


async def test_the_music_entry_reaches_lux_with_the_music_frame() -> None:
    posted = await _registered_music_entry()

    assert [r.callback.frame_id for r in posted] == ["vox.music"]


async def test_the_panel_entry_reaches_lux_with_the_panel_frame() -> None:
    posted = await _registered_panel_entry(_panel_service())

    assert [r.callback.frame_id for r in posted] == ["vox.panel"]


async def test_the_music_entry_reopens_its_scenes_scoped_frame() -> None:
    """The Hub scopes both ids to the connection; the scoped keys must match.

    A click reopens the frame the Hub derives from the entry, and the scene sits
    in the frame the Hub derives from the render request, so the two derivations
    are compared through lux's own code rather than as raw strings.
    """
    frames = _ScopedFrames("conn-music")
    scene = AlbumListScene(
        AlbumRoster.from_cache((), TrackCountCache()), PlayerView.idle()
    )

    (registered,) = await _registered_music_entry()

    assert frames.of_entry(registered) == frames.of_scene(scene.render_request())


async def test_the_panel_entry_reopens_its_scenes_scoped_frame() -> None:
    """The panel's entry and its scene resolve to one connection-scoped frame."""
    frames = _ScopedFrames("conn-panel")
    service = _panel_service()

    (registered,) = await _registered_panel_entry(service)

    assert frames.of_entry(registered) == frames.of_scene(
        service.scene().render_request()
    )
