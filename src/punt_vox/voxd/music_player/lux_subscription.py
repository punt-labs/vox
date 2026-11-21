"""``LuxSubscription`` -- voxd's receive leg: one hub connection, menu, dispatch.

The subscription holds a *single* live connection to luxd at a time (Z model
invariant I). It subscribes to every :class:`MusicTopic` -- the album-list
``music.play`` / ``music.stop`` and the transport bar's ``music.prev`` /
``music.pause`` / ``music.resume`` / ``music.next`` -- and holds the
connection open; the ``LuxHubClient`` reconnects and re-subscribes internally across
transient drops, firing the subscription's ``on_connect`` hook after *every*
successful handshake -- first connect and every internal reconnect. That hook
re-registers the ``Music`` menu entry and re-pushes the scene, so a >30s luxd outage
that lapses the menu lease (swept by luxd) is healed the instant the listener rejoins
internally, without waiting for an outer fault (invariant III, register-fresh). A
guarded restart loop still wraps the whole connect/subscribe/listen cycle as a
backstop: a fault the internal reconnect cannot ride out -- a down luxd, or a
protocol frame that fails validation deep inside ``listen`` -- is logged to the
persistent daemon log and the cycle restarts after a backoff, so the receive leg can
never die silently. Each inbound event is decoded and applied exactly once
(invariants II, V). Every handler is a boundary that logs and drops on any fault, so
one bad frame can never drop the connection.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Self, final

from punt_vox.lux_common import HubOutageLog, MenuEntry
from punt_vox.voxd.music_player.lux_fault_boundary import LuxFaultBoundary
from punt_vox.voxd.music_player.lux_receive_retry import LuxReceiveRetry
from punt_vox.voxd.music_player.lux_trace import LuxTrace
from punt_vox.voxd.music_player.music_event_receiver import MusicEventReceiver
from punt_vox.voxd.music_player.wire import MusicTopic

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from punt_lux import CallbackHandler, EventHandler
    from punt_lux.hub_client import ConnectHandler

    from punt_vox.voxd.music_player.command_ports import ProgramSeam
    from punt_vox.voxd.music_player.hub_ports import HubListener, MenuRegistrar
    from punt_vox.voxd.music_player.presenter_ports import ScenePresenter

__all__ = ["LuxSubscription"]

logger = logging.getLogger(__name__)
_trace = LuxTrace(logger)

_RETRY_SECONDS = 5.0


@final
class LuxSubscription:
    """Own voxd's one hub connection, the ``Music`` menu, and event dispatch."""

    __slots__ = (
        "_connect_hub",
        "_entry",
        "_events",
        "_menu",
        "_presenter",
        "_retry",
    )
    _presenter: ScenePresenter
    _menu: MenuRegistrar
    _entry: MenuEntry
    _connect_hub: Callable[[EventHandler, CallbackHandler, ConnectHandler], HubListener]
    _events: MusicEventReceiver
    _retry: LuxReceiveRetry

    def __new__(
        cls,
        service: ProgramSeam,
        presenter: ScenePresenter,
        menu: MenuRegistrar,
        connect_hub: Callable[
            [EventHandler, CallbackHandler, ConnectHandler], HubListener
        ],
        # None means the receive leg owns its own outage window; the composition
        # injects a shared instance so the menu registrar's best-effort I/O and
        # this retry loop escalate one continuous outage together.
        outage: HubOutageLog | None = None,
    ) -> Self:
        """Create a lux subscription."""
        self = super().__new__(cls)
        self._presenter = presenter
        self._menu = menu
        self._entry = MenuEntry("music", "Music", presenter.frame_id())
        self._connect_hub = connect_hub
        self._events = MusicEventReceiver(service, presenter)
        self._retry = LuxReceiveRetry(
            outage if outage is not None else HubOutageLog(logger)
        )
        return self

    async def run(self) -> None:
        """Hold voxd's receive leg open, restarting the whole cycle on any fault.

        Each iteration builds one fresh connection, subscribes, and listens; the
        ``Music`` menu registration and the scene re-push ride the ``on_connect``
        hook the hub client fires on every handshake (:meth:`on_connect`), not this
        outer loop. ``listen`` returns only when the daemon requests a stop, so a
        clean return ends the leg. A down luxd is retried with a warning; any other
        fault -- notably a protocol frame that fails validation deep inside
        ``listen`` -- is logged without frame details to the persistent daemon log and
        the cycle restarts after a backoff, so a transient or protocol error can
        never leave the receive leg silently dead (invariants I, III). Cancellation
        on shutdown is a ``BaseException`` that propagates cleanly out.
        """

        async def listen() -> None:
            listener = self._connect_hub(
                self.on_event, self.on_callback, self.on_connect
            )
            listener.subscribe(*MusicTopic)
            _trace.debug(
                "subscribed to topics %s; listening",
                ", ".join(topic.value for topic in MusicTopic),
            )
            await listener.listen()

        await self._retry.run(listen, _RETRY_SECONDS)

    async def on_event(self, topic: str, payload: Mapping[str, object]) -> None:
        """Deliver a frame to the privacy-safe event boundary."""
        self._events.receive(topic, payload)

    async def on_callback(self, callback_id: str) -> None:
        """Install the music scene when the ``Music`` menu entry is clicked.

        This is one of the two sites that install rather than refresh, and the
        reason is the whole point of the entry: the user clicked *Music* to see
        the window, so showing its whole scene is the answer, not a side effect.
        """
        if not self._entry.owns(callback_id):
            return
        _trace.info("Music menu clicked; installing the scene")
        with LuxFaultBoundary(logger, "music menu open failed"):
            await self._presenter.install()

    async def on_connect(self) -> None:
        """Re-register the ``Music`` menu and install the scene after every handshake.

        The hub client fires this once per successful handshake -- first connect and
        every internal reconnect -- after the ready frame and re-subscribe. A >30s
        luxd outage lapses the menu lease, luxd sweeps the entry, and the internal
        reconnect ``listen`` rides out then fires this to restore it, without waiting
        for an outer fault (register-fresh, invariant III). The registration is
        best-effort and never raises; the scene push is guarded here so a transient
        projection failure is logged, not lost, and never skips the registration. lux
        logs-and-continues if this raises, so the session survives regardless.

        The push installs rather than refreshes: this connection carries nothing
        yet, so there is no installed tree for a patch to write onto.
        """
        # The handshake is the ground-truth "luxd is back" signal for both legs,
        # so it closes the shared outage window before the menu register runs.
        self._retry.connected()
        await self._menu.register(self._entry)
        with LuxFaultBoundary(logger, "music scene projection on connect failed"):
            await self._presenter.install()
