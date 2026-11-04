"""Tests for the presenter ports: the real presenter honours ``ScenePresenter``.

``ScenePresenter.frame_id`` is the frame the subscription names when it registers
the Music menu, so it must be the frame the presenter's scene actually lands in --
read here through lux's own ``RenderRequest.presentation`` from the requests
:class:`MusicPlayer` publishes, not compared as a literal. That the registered
entry's frame and the scene's frame match once the Hub scopes both to a
connection is pinned in ``tests/test_lux_common/test_menu_frame_contract.py``.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Never, final

from punt_vox.types_programs.status import ProgramStatus
from punt_vox.voxd.music_player.lux_subscription import LuxSubscription
from punt_vox.voxd.music_player.player import MusicPlayer
from punt_vox.voxd.music_player.presenter_ports import (
    FailurePresenter,
    ScenePresenter,
)
from punt_vox.voxd.music_player.scene import AlbumListScene

if TYPE_CHECKING:
    from punt_lux import CallbackHandler, EventHandler, RenderRequest
    from punt_lux.hub_client import ConnectHandler

    from punt_vox.lux_common import MenuEntry
    from punt_vox.voxd.programs.album_id import AlbumId
    from punt_vox.voxd.programs.catalog import Album

# Ample time for the background track-count refresh a projection schedules (an
# empty catalog, no disk) to finish, so no task outlives its test.
_SETTLE_SECONDS = 0.05


@final
class _IdleProgram:
    """A ProgramSeam double: an idle daemon with an empty catalog, commands inert."""

    def status(self) -> ProgramStatus:
        return ProgramStatus.idle()

    def catalog_albums(self) -> tuple[Album, ...]:
        return ()

    def replay_album(self, album_id: AlbumId) -> None:
        _ = album_id

    def stop(self) -> None:
        pass

    def advance(self) -> None:
        pass

    def prev(self) -> None:
        pass

    def pause(self) -> None:
        pass

    def resume(self) -> None:
        pass


@final
class _CapturingPublisher:
    """A ScenePublisher double recording every request, refresh or install."""

    def __init__(self) -> None:
        self.published: list[RenderRequest] = []

    def submit(self, request: RenderRequest) -> None:
        self.published.append(request)

    def reinstall(self, request: RenderRequest) -> None:
        self.published.append(request)

    def frames(self) -> set[str]:
        """Return each frame a published scene lands in, as lux derives it."""
        return {request.presentation().frame_id for request in self.published}


@final
class _RecordingMenu:
    """A MenuRegistrar double recording each entry it is asked to register."""

    def __init__(self) -> None:
        self.entries: list[MenuEntry] = []

    async def register(self, entry: MenuEntry) -> None:
        self.entries.append(entry)


def _no_hub(
    on_event: EventHandler, on_callback: CallbackHandler, on_connect: ConnectHandler
) -> Never:
    """Refuse a hub connection; the handshake hook under test needs none."""
    _ = (on_event, on_callback, on_connect)
    msg = "the handshake hook must not dial the hub"
    raise AssertionError(msg)


def _presenter(publisher: _CapturingPublisher) -> ScenePresenter:
    # Returned as the port, so mypy and pyright hold MusicPlayer to it.
    return MusicPlayer(_IdleProgram(), publisher)


async def _settle() -> None:
    await asyncio.sleep(_SETTLE_SECONDS)


def test_the_music_player_is_a_scene_presenter() -> None:
    assert isinstance(_presenter(_CapturingPublisher()), ScenePresenter)


def test_the_music_player_is_a_failure_presenter() -> None:
    assert isinstance(_presenter(_CapturingPublisher()), FailurePresenter)


def test_the_music_players_frame_is_its_scenes_frame() -> None:
    assert _presenter(_CapturingPublisher()).frame_id() == AlbumListScene.FRAME_ID


async def test_an_installed_scene_lands_in_the_presenters_frame() -> None:
    publisher = _CapturingPublisher()
    presenter = _presenter(publisher)

    await presenter.install()
    await _settle()

    assert publisher.frames() == {presenter.frame_id()}


async def test_a_refreshed_scene_lands_in_the_presenters_frame() -> None:
    publisher = _CapturingPublisher()
    presenter = _presenter(publisher)

    presenter.notify_changed()
    presenter.present_stop_failure()
    presenter.present_transport_failure()
    presenter.present_resolve_failure("Gone Album")
    await _settle()

    assert publisher.frames() == {presenter.frame_id()}


async def test_the_handshake_registers_the_menu_for_the_installed_frame() -> None:
    # The subscription derives the menu entry's frame from the presenter, so on a
    # handshake the entry it registers reopens the very frame it installs.
    publisher = _CapturingPublisher()
    menu = _RecordingMenu()
    subscription = LuxSubscription(_IdleProgram(), _presenter(publisher), menu, _no_hub)

    await subscription.on_connect()
    await _settle()

    (installed,) = publisher.frames()
    assert [entry.frame_id for entry in menu.entries] == [installed]
