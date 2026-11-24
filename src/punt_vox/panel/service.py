"""``VoxPanelService`` -- the ``Vox`` menu entry a session owns.

Reads vox's current settings, applies a control change to the same config store
and daemon RPCs the CLI and MCP tool already use, and pushes the confirmed scene.
It follows the phases of :class:`punt_lux.applets.AppletService` (an ``entry``,
``prefetch``, ``acknowledge``, ``service``) without satisfying that protocol:
its ``acknowledge`` and ``service`` are coroutines over a ``LuxClient``, so
:class:`~punt_vox.panel.leg.VoxPanelLeg` drives it, along with the extra methods
the leg calls when a control event arrives.

The panel can handle menu and control events concurrently on worker threads.
:class:`~punt_vox.panel.panel_settings.PanelSettings` holds the confirmed
settings and notice and serializes their updates.

Reaching luxd is :class:`~punt_vox.panel.panel_push.PanelPush`'s job, not this
one's. The two verbs it exposes differ in intent rather than content:
:meth:`VoxPanelService.install_scene` shows the whole scene, and is reached only
from the ``Vox`` menu click; :meth:`VoxPanelService.push_scene` refreshes the
panel where it already sits, which is what the confirm push behind a click and
every control-change re-push want. Radio clicks take the refresh path: a ``show``
replaces the whole tree, where a patch leaves focus and tab state alone.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Self, final

from punt_vox.lux_common import MenuEntry
from punt_vox.panel.click_target import ClickTarget
from punt_vox.panel.control_push import ControlPush
from punt_vox.panel.panel_notice import PanelNotice
from punt_vox.panel.panel_push import PanelPush
from punt_vox.panel.panel_scene import PanelScene
from punt_vox.panel.panel_settings import PanelSettings
from punt_vox.panel.radio_control import MIC_MODE_SPEC, NOTIFY_SPEC
from punt_vox.panel.settings_refresh import RefreshNotices, SettingsRefresh
from punt_vox.panel.state import PanelState
from punt_vox.panel.topics import PanelTopic
from punt_vox.panel.voice_preview import VoicePreview

if TYPE_CHECKING:
    from collections.abc import Mapping

    from punt_lux import LuxClient
    from punt_lux.applets import ClickLatency

    from punt_vox.panel.ports import PanelDaemonClient, SettingsStore

logger = logging.getLogger(__name__)

__all__ = ["VoxPanelService"]

# The frame is the scene's own, so a click on the entry reopens the window the
# scene was pushed into after the user closed it.
_ENTRY = MenuEntry("vox-panel", "Vox", PanelScene.FRAME_ID)


@final
class VoxPanelService:
    """A session's Vox settings entry: read settings, apply a change, show the scene."""

    _refresh: SettingsRefresh
    _settings: PanelSettings
    _push: PanelPush
    _voice_preview: VoicePreview
    __slots__ = (
        "_push",
        "_refresh",
        "_settings",
        "_voice_preview",
    )

    def __new__(cls, client: PanelDaemonClient, store: SettingsStore) -> Self:
        """Create a vox panel service."""
        self = super().__new__(cls)
        self._settings = PanelSettings(client, store)
        self._refresh = SettingsRefresh(client, store)
        self._push = PanelPush()
        self._voice_preview = VoicePreview(client, store, logger)
        return self

    @property
    def entry(self) -> MenuEntry:
        """The ``Vox`` menu entry this session registers, and whose clicks it serves."""
        return _ENTRY

    def prefetch(self) -> None:
        """Read settings once before any click, so the first click has some to show."""
        self.refresh()

    async def acknowledge(self, client: LuxClient, latency: ClickLatency) -> None:
        """Install the held scene now -- the visible half of the click.

        This is the ``Vox`` menu entry answering, so showing the scene is the
        answer, not a side effect.
        """
        with latency.answering():
            await self.install_scene(client)

    async def service(self, client: LuxClient, latency: ClickLatency) -> None:
        """Re-read settings fresh and refresh the scene with the confirmed ones.

        A few milliseconds behind :meth:`acknowledge`, onto the same window it
        just showed -- so this refreshes rather than installing again.
        """
        with latency.stage("refreshed"):
            await asyncio.to_thread(self.refresh)
        await self.push_scene(client)

    def scene(self) -> PanelScene:
        """Return the currently-held settings and notice as a scene."""
        return self._settings.scene()

    async def install_scene(self, client: LuxClient) -> None:
        """Show the held scene outright, the whole tree at once (a menu click)."""
        await self._push.install(client, self._settings.scene().render_request())

    async def push_scene(self, client: LuxClient, *, background: bool = False) -> None:
        """Refresh the installed scene with the currently-held one."""
        await self._push.refresh(
            client, self._settings.scene().render_request(), background=background
        )

    async def correct_scene(self, client: LuxClient) -> None:
        """Reinstall the held scene in full, snapping back an optimistic widget.

        Reached only after a control-change failure that a widget already
        applied client-side before voxd answered -- see
        :class:`~punt_vox.panel.panel_push.PanelPush` for why a diff-based
        refresh cannot express this correction.
        """
        await self.install_scene(client)

    def refresh(self, *, external: bool = False) -> bool:
        """Re-read settings from disk and voxd; note staleness if voxd is down."""
        return self._refresh.refresh(self._settings, external=external)

    async def reconcile(self) -> bool:
        """Check external settings for an installed panel; report if it needs a push."""
        if not self._push.has_confirmed_scene:
            return False
        return await asyncio.to_thread(self.refresh, external=True)

    def recover_from_write_failure(self, field: str) -> None:
        """Re-sync from the real settings after a failed persist, and flag the scene."""
        self._refresh.refresh(self._settings, RefreshNotices.write_failure(field))

    def note_rejection(self, detail: str) -> None:
        """Flag the scene with voxd's refusal, keeping the last-known settings.

        Unlike :meth:`recover_from_write_failure` this does not re-sync:
        voxd has just proved it answers this session with a refusal, so a
        confirming read would either fail the same way or trade this
        specific reason for a generic one.
        """
        self._settings.note(PanelNotice.voxd_rejected(detail))

    def note_control_rejected(self, control: str) -> None:
        """Flag the scene with a control change that could not be applied.

        *control* is the control's human name, as
        :attr:`~punt_vox.panel.topics.PanelTopic.label` gives it. Reached
        from :class:`~punt_vox.panel.panel_runner.PanelRunner` when
        :meth:`apply_event` refuses an event, so the corrective reinstall
        that follows carries a reason instead of an unexplained revert.
        """
        self._settings.note(PanelNotice.control_rejected(control))

    def apply_event(self, topic: str, payload: Mapping[str, object]) -> ControlPush:
        """Apply one control-topic event; answer what kind of re-push it needs.

        A payload rejection (``TypeError``/``ValueError``), a value the store
        will not serialize (``ConfigValueError``), and a config-write failure
        (``OSError``) all propagate -- this method swallows none of them, so
        :class:`~punt_vox.panel.panel_runner.PanelRunner` can answer each.

        The three-way answer -- :attr:`~ControlPush.NONE`,
        :attr:`~ControlPush.REFRESH`, :attr:`~ControlPush.CORRECT` -- is not
        just "did the scene change": provider/model commits
        can abandon a commit (an unreadable roster, a provider swapped
        mid-fetch) without raising, leaving confirmed settings where they were.
        That abandonment is still a widget the caller already updated
        optimistically and this service never confirmed, so it answers
        :attr:`~ControlPush.CORRECT` like every raising failure does -- a
        bare re-derived ``bool`` cannot tell that apart from a genuine no-op.
        """
        if topic == PanelTopic.NOTIFY:
            return self._settings.commit_notify(
                NOTIFY_SPEC.code_for_index(self._index(payload))
            )
        if topic == PanelTopic.MIC_MODE:
            code = MIC_MODE_SPEC.code_for_index(self._index(payload))
            self._settings.commit("speak", code, PanelState.with_speak)
            return ControlPush.REFRESH
        if topic == PanelTopic.VOICE:
            return self._commit_voice(self._index(payload))
        if topic == PanelTopic.PROVIDER:
            return self._settings.commit_provider(
                self._target().provider(self._index(payload))
            )
        if topic == PanelTopic.MODEL:
            return self._settings.commit_model(
                self._target().model(self._index(payload))
            )
        if topic == PanelTopic.VOICE_PREVIEW:
            return self._preview()
        logger.warning("vox-panel: no handler for topic %r", topic)
        return ControlPush.NONE

    def _commit_voice(self, index: int) -> ControlPush:
        state = self._settings.snapshot()
        return self._settings.commit_voice(state, ClickTarget(state).voice(index))

    def _target(self) -> ClickTarget:
        """Snapshot the held settings as the thing a click's index names."""
        return ClickTarget(self._settings.snapshot())

    def _preview(self) -> ControlPush:
        """Play the held voice back; answer the push the notice band needs.

        :class:`~punt_vox.panel.voice_preview.VoicePreview` speaks the line
        and names the notice its outcome earns; this method only decides what
        that notice does to the band.

        A preview the caller just *heard* is the strongest evidence there is
        that voxd is reachable, so success clears any warning still standing
        -- otherwise a "voxd is unreachable" line outlives its own cause,
        contradicted by the audio that just played. Every outcome here says
        something, including the one where no voice is selected: a button
        press that changes nothing on screen reads as a broken panel.
        """
        voice = self._settings.snapshot().voice
        if voice is None:
            logger.info("vox-panel: no voice selected yet; preview skipped")
            self._settings.note(PanelNotice.no_voice_selected())
            return ControlPush.REFRESH
        return self._settings.note_preview(self._voice_preview.play(voice))

    @staticmethod
    def _index(payload: Mapping[str, object]) -> int:
        """Return the payload's int ``value``, or raise if it is missing/wrong-typed."""
        value = payload.get("value")
        if not isinstance(value, int) or isinstance(value, bool):
            msg = f"expected an int 'value' in the control payload, got {value!r}"
            raise TypeError(msg)
        return value
