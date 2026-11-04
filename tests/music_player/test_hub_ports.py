"""Tests for the hub ports: the production transports conform to each seam.

Behaviour behind the seams is pinned beside each implementation: that
``LuxMenuRegistrar`` hands lux the whole :class:`MenuEntry` in ``test_lux_menu.py``,
:class:`LuxClientFactory` conformance in ``test_lux_clients.py``, and that lux
accepts the frame vox sends in ``tests/test_lux_common/test_menu_frame_contract.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from punt_lux.hub_client import LuxHubClient

from punt_vox.voxd.music_player.hub_ports import HubListener, MenuRegistrar
from punt_vox.voxd.music_player.lux_menu import LuxMenuRegistrar

if TYPE_CHECKING:
    from punt_lux import LuxClient

# Annotated as the port, so mypy and pyright check the class against its seam.
# The class, not an instance: a hub client would dial luxd on construction.
_LISTENER: type[HubListener] = LuxHubClient


def _registrar() -> MenuRegistrar:
    # Returned as the port, so the type checkers hold LuxMenuRegistrar to it. The
    # connect callable is never invoked: construction does not reach lux.
    return LuxMenuRegistrar(lambda: cast("LuxClient", object()))


def test_the_lux_hub_client_is_a_hub_listener() -> None:
    assert issubclass(_LISTENER, HubListener)


def test_the_lux_registrar_is_a_menu_registrar() -> None:
    assert isinstance(_registrar(), MenuRegistrar)
