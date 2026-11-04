"""Tests for the hub ports: the production transports honour each seam's contract.

:class:`MenuRegistrar` takes a whole :class:`MenuEntry`, so the registrar may not
pick its own callback id, label, or frame: it hands lux exactly the entry it was
given. That lux accepts what vox sends, and that the music and panel entries name
the frames their scenes land in, is pinned against lux's real request validation
in ``tests/test_lux_common/test_menu_frame_contract.py``; this module pins the
seams themselves. :class:`LuxClientFactory` conformance is pinned beside its one
implementation, in ``test_lux_clients.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast, final

import pytest
from punt_lux.hub_client import LuxHubClient
from punt_lux.operations import Ok

from punt_vox.lux_common import MenuEntry
from punt_vox.panel.panel_scene import PanelScene
from punt_vox.voxd.music_player.hub_ports import HubListener, MenuRegistrar
from punt_vox.voxd.music_player.lux_menu import LuxMenuRegistrar
from punt_vox.voxd.music_player.scene import AlbumListScene

if TYPE_CHECKING:
    from punt_lux import LuxClient, OpError

# Annotated as the port, so mypy and pyright check the class against its seam.
# The class, not an instance: a hub client would dial luxd on construction.
_LISTENER: type[HubListener] = LuxHubClient


@final
class _RecordingCallbacks:
    """The ``LuxClient.callback`` accessor, recording each registration it is sent."""

    def __init__(self) -> None:
        self.sent: list[MenuEntry] = []

    async def register(
        self, callback_id: str, label: str, frame_id: str | None = None
    ) -> Ok | OpError:
        assert frame_id is not None, "a menu entry must name the frame it reopens"
        self.sent.append(MenuEntry(callback_id, label, frame_id))
        return Ok()


@final
class _RecordingClient:
    """A ``LuxClient`` stand-in carrying only the callback accessor."""

    def __init__(self) -> None:
        self.callback = _RecordingCallbacks()


def _registrar(client: _RecordingClient) -> MenuRegistrar:
    # Returned as the port, so the type checkers hold LuxMenuRegistrar to it.
    return LuxMenuRegistrar(lambda: cast("LuxClient", client))


def test_the_lux_hub_client_is_a_hub_listener() -> None:
    assert issubclass(_LISTENER, HubListener)


def test_the_lux_registrar_is_a_menu_registrar() -> None:
    assert isinstance(_registrar(_RecordingClient()), MenuRegistrar)


@pytest.mark.parametrize(
    "entry",
    [
        MenuEntry("music", "Music", AlbumListScene.FRAME_ID),
        MenuEntry("vox-panel", "Vox", PanelScene.FRAME_ID),
        MenuEntry("another", "Another", "some.other.frame"),
    ],
    ids=["music", "panel", "arbitrary"],
)
async def test_register_hands_lux_the_whole_entry(entry: MenuEntry) -> None:
    client = _RecordingClient()

    await _registrar(client).register(entry)

    assert client.callback.sent == [entry]
