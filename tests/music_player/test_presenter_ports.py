"""Tests for the presenter ports: the real presenter honours ``ScenePresenter``.

``ScenePresenter.frame_id`` is the frame the subscription names when it registers
the Music menu, so it must be the frame the presenter's scene actually lands in --
read here through lux's own ``RenderRequest.presentation`` from the requests
:class:`MusicPlayer` publishes, not compared as a literal. Each presenter verb
publishes exactly one request, by its own operation: ``install`` reinstalls the
whole scene, every other verb submits a refresh. That the registered entry's
frame and the scene's frame match once the Hub scopes both to a connection is
pinned in ``tests/test_lux_common/test_menu_frame_contract.py``.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Literal, Never, final

import pytest

from punt_vox.types_programs.status import ProgramStatus
from punt_vox.voxd.music_player.lux_subscription import LuxSubscription
from punt_vox.voxd.music_player.player import MusicPlayer
from punt_vox.voxd.music_player.presenter_ports import (
    FailurePresenter,
    ScenePresenter,
)
from punt_vox.voxd.programs.album_id import AlbumId

if TYPE_CHECKING:
    from collections.abc import Callable

    from punt_lux import CallbackHandler, EventHandler, RenderRequest
    from punt_lux.hub_client import ConnectHandler

    from punt_vox.lux_common import MenuEntry
    from punt_vox.voxd.programs.catalog import Album

type _Operation = Literal["submit", "reinstall"]


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
    """A ScenePublisher double recording each request with the operation it rode."""

    def __init__(self) -> None:
        self.published: list[tuple[_Operation, RenderRequest]] = []

    def submit(self, request: RenderRequest) -> None:
        self.published.append(("submit", request))

    def reinstall(self, request: RenderRequest) -> None:
        self.published.append(("reinstall", request))

    def landed(self) -> list[tuple[_Operation, str]]:
        """Return each publish's operation and the frame lux derives for it."""
        return [(op, request.presentation().frame_id) for op, request in self.published]


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


async def _drain() -> None:
    """Await every background task the projection started, until none remain.

    Each verb schedules a track-count refresh that resubmits when it lands; the
    tests count publishes before draining, so the refresh never pads the count.
    """
    while pending := asyncio.all_tasks() - {asyncio.current_task()}:
        await asyncio.gather(*pending)


def test_the_music_player_is_a_scene_presenter() -> None:
    assert isinstance(_presenter(_CapturingPublisher()), ScenePresenter)


def test_the_music_player_is_a_failure_presenter() -> None:
    assert isinstance(_presenter(_CapturingPublisher()), FailurePresenter)


async def test_install_reinstalls_one_scene_in_the_presenters_frame() -> None:
    publisher = _CapturingPublisher()
    presenter = _presenter(publisher)

    await presenter.install()
    landed = publisher.landed()
    await _drain()

    assert landed == [("reinstall", presenter.frame_id())]


# Every presenter verb but install: each is a refresh, never a reinstall.
_REFRESHES: dict[str, Callable[[ScenePresenter], None]] = {
    "notify_changed": lambda p: p.notify_changed(),
    "present_play_failure": lambda p: p.present_play_failure(AlbumId("aa11bb")),
    "present_stop_failure": lambda p: p.present_stop_failure(),
    "present_transport_failure": lambda p: p.present_transport_failure(),
    "present_resolve_failure": lambda p: p.present_resolve_failure("Gone Album"),
}


@pytest.mark.parametrize(
    "refresh", list(_REFRESHES.values()), ids=list(_REFRESHES.keys())
)
async def test_a_refresh_submits_one_scene_in_the_presenters_frame(
    refresh: Callable[[ScenePresenter], None],
) -> None:
    publisher = _CapturingPublisher()
    presenter = _presenter(publisher)

    refresh(presenter)
    landed = publisher.landed()
    await _drain()

    assert landed == [("submit", presenter.frame_id())]


async def test_the_handshake_registers_the_menu_for_the_installed_frame() -> None:
    # The subscription derives the menu entry's frame from the presenter, so on a
    # handshake the entry it registers reopens the very frame it installs.
    publisher = _CapturingPublisher()
    menu = _RecordingMenu()
    subscription = LuxSubscription(_IdleProgram(), _presenter(publisher), menu, _no_hub)

    await subscription.on_connect()
    installed = [frame for op, frame in publisher.landed() if op == "reinstall"]
    await _drain()

    assert [entry.frame_id for entry in menu.entries] == installed
    assert len(installed) == 1
