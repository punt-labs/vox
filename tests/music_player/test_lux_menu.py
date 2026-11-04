"""Tests for LuxMenuRegistrar: the guarded, failure-tolerant menu registration."""

from __future__ import annotations

import logging
from typing import cast, final

import pytest
from punt_lux import HubUnavailableError, LuxClient, OpError
from punt_lux.operations import Ok

from punt_vox.lux_common import HubOutageLog, MenuEntry
from punt_vox.panel.panel_scene import PanelScene
from punt_vox.voxd.music_player.lux_menu import LuxMenuRegistrar


@final
class _OkCallbackAccessor:
    """Record each registration's raw arguments; the test body asserts on them.

    Asserting in here would be swallowed: the registrar logs and drops any
    exception its client raises.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str | None]] = []

    async def register(
        self, callback_id: str, label: str, frame_id: str | None = None
    ) -> Ok | OpError:
        self.calls.append((callback_id, label, frame_id))
        return Ok()


@final
class _OkClient:
    """A LuxClient stand-in whose callback accessor records and reports success."""

    def __init__(self) -> None:
        self.callback = _OkCallbackAccessor()


@final
class _RefusingCallbackAccessor:
    async def register(
        self, callback_id: str, label: str, frame_id: str | None = None
    ) -> Ok | OpError:
        return OpError(code="invalid_request", reason="bad menu id")


@final
class _RefusingClient:
    def __init__(self) -> None:
        self.callback = _RefusingCallbackAccessor()


_MUSIC = MenuEntry("music", "Music", "vox.music")


def _as_client(fake: object) -> LuxClient:
    """Cast a duck-typed menu stand-in to the LuxClient type the seam wants."""
    return cast("LuxClient", fake)


@pytest.mark.parametrize(
    "entry",
    [
        _MUSIC,
        MenuEntry("vox-panel", "Vox", PanelScene.FRAME_ID),
        MenuEntry("another", "Another", "some.other.frame"),
    ],
    ids=["music", "panel", "arbitrary"],
)
async def test_register_hands_lux_the_whole_entry(entry: MenuEntry) -> None:
    # The arbitrary entry is what catches a registrar that names its own frame.
    client = _OkClient()

    await LuxMenuRegistrar(lambda: _as_client(client)).register(entry)

    assert client.callback.calls == [(entry.callback_id, entry.label, entry.frame_id)]


async def test_register_logs_the_success_with_the_callback_id(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO):
        await LuxMenuRegistrar(lambda: _as_client(_OkClient())).register(_MUSIC)
    registered = [
        r
        for r in caplog.records
        if "[lux]" in r.getMessage() and "registered" in r.getMessage()
    ]
    assert registered
    assert "'music'" in registered[-1].getMessage()  # the callback id is logged


async def test_register_swallows_a_down_luxd(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def connect() -> LuxClient:
        raise HubUnavailableError("luxd down")

    with caplog.at_level(logging.WARNING):
        await LuxMenuRegistrar(connect).register(_MUSIC)  # must not raise

    assert any("not registered" in r.getMessage() for r in caplog.records)


async def test_register_throttles_repeat_outage_ticks_to_debug(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # A luxd outage that repeats registration attempts must not spam WARNING per
    # tick: the shared HubOutageLog opens the outage at WARNING once and quiets
    # subsequent ticks to DEBUG until the outage clears.
    def connect() -> LuxClient:
        raise HubUnavailableError("luxd down")

    registrar = LuxMenuRegistrar(connect)
    with caplog.at_level(logging.DEBUG):
        await registrar.register(_MUSIC)
        await registrar.register(_MUSIC)
        await registrar.register(_MUSIC)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    debugs = [
        r
        for r in caplog.records
        if r.levelno == logging.DEBUG and "not registered" in r.getMessage()
    ]
    assert len(warnings) == 1  # first tick loud, later ticks quiet
    assert len(debugs) == 2  # subsequent ticks throttled to DEBUG


async def test_register_shares_outage_window_with_the_subscription(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The composition injects one HubOutageLog into both the receive leg and this
    # registrar. Once the subscription's retry has already opened the outage at
    # WARNING, the registrar's own tick during the same outage must not re-open a
    # second WARNING -- it inherits the ongoing window and logs at DEBUG instead.
    def connect() -> LuxClient:
        raise HubUnavailableError("luxd down")

    shared = HubOutageLog(logging.getLogger("test.shared-outage"))
    shared.note("[lux] subscription opened the outage first")  # pretend sub ticked

    registrar = LuxMenuRegistrar(connect, outage=shared)
    with caplog.at_level(logging.DEBUG, logger="test.shared-outage"):
        await registrar.register(_MUSIC)

    warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "not registered" in r.getMessage()
    ]
    debugs = [
        r
        for r in caplog.records
        if r.levelno == logging.DEBUG and "not registered" in r.getMessage()
    ]
    assert warnings == []  # the shared window is already open, no fresh WARNING
    assert len(debugs) == 1  # the registrar's tick lands at DEBUG


async def test_register_logs_a_refused_registration(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.ERROR):
        await LuxMenuRegistrar(lambda: _as_client(_RefusingClient())).register(_MUSIC)

    rejections = [
        r.getMessage() for r in caplog.records if "rejected" in r.getMessage()
    ]
    assert len(rejections) == 1
    assert "'vox.music'" in rejections[0]
