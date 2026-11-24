"""Session settings and atomic provider/model cascade commits."""

from __future__ import annotations

import logging
import threading
from dataclasses import replace
from typing import TYPE_CHECKING, Self, final

from punt_vox.cascade import Cascade, RosterError, RosterRejectedError
from punt_vox.panel.control_push import ControlPush
from punt_vox.panel.panel_notice import PanelNotice
from punt_vox.panel.state import PanelState
from punt_vox.voice_target import VoiceTarget

if TYPE_CHECKING:
    from collections.abc import Callable

    from punt_vox.panel.panel_scene import PanelScene
    from punt_vox.panel.ports import SettingsStore
    from punt_vox.voice_target import RosterClient

logger = logging.getLogger(__name__)

__all__ = ["PanelSettings"]


@final
class PanelSettings:
    """Hold a panel's settings and notice with one lock for atomic commits."""

    _client: RosterClient
    _store: SettingsStore
    _lock: threading.Lock
    _state: PanelState
    _notice: PanelNotice
    __slots__ = ("_client", "_lock", "_notice", "_state", "_store")

    def __new__(cls, client: RosterClient, store: SettingsStore) -> Self:
        """Create empty settings for a session."""
        self = super().__new__(cls)
        self._client = client
        self._store = store
        self._lock = threading.Lock()
        self._state = PanelState.empty()
        self._notice = PanelNotice.silent()
        return self

    def snapshot(self) -> PanelState:
        """Read the last confirmed settings."""
        with self._lock:
            return self._state

    def scene(self) -> PanelScene:
        """Read the confirmed settings and notice together."""
        with self._lock:
            return replace(self._state.scene(), notice=self._notice)

    def replace_if_current(
        self, before: PanelState, state: PanelState, notice: PanelNotice
    ) -> None:
        """Install a resync only if nothing committed while it fetched voices."""
        with self._lock:
            if self._state is before:
                self._state, self._notice = state, notice

    def note(self, notice: PanelNotice) -> None:
        """Set the notice without changing the confirmed settings."""
        with self._lock:
            self._notice = notice

    def note_preview(self, notice: PanelNotice) -> ControlPush:
        """Apply a preview outcome and report whether the notice band changed."""
        with self._lock:
            if not notice.is_present:
                return self._cleared_notice()
            self._notice = notice
            return ControlPush.REFRESH

    def commit(
        self, field: str, value: str, update: Callable[[PanelState, str], PanelState]
    ) -> None:
        """Persist *field* and update the held state as one atomic step."""
        with self._lock:
            self._store.write_field(field, value)
            self._state = update(self._state, value)
            self._notice = PanelNotice.silent()

    def _fetch_roster_or_notice(
        self, target: VoiceTarget, context: str
    ) -> tuple[str, ...] | RosterError:
        """Fetch *provider*'s voice roster or return the reported daemon fault.

        Failures set a notice under the lock, preserving the daemon's reason
        when it rejected the request. The caller aborts its commit.
        """
        result = Cascade.fetch_roster(self._client, target)
        if isinstance(result, RosterError):
            logger.warning(
                "vox-panel: roster fetch failed during %s (provider=%r): %s",
                context,
                target.provider,
                result.message,
            )
            # A daemon-sent rejection carries the reason voxd refused (an
            # unknown provider name, a wire mismatch from a long-lived
            # panel talking to a fresh strict daemon) — surface it
            # verbatim so the operator sees WHY the roster is missing
            # rather than a generic "unavailable" line that reads as
            # "the daemon is down" when the daemon is right there and
            # said no.
            with self._lock:
                if isinstance(result, RosterRejectedError):
                    self._notice = PanelNotice.voxd_rejected(result.message)
                else:
                    self._notice = PanelNotice.voxd_unavailable()
            return result
        return tuple(result)

    def commit_provider(self, provider: str) -> ControlPush:
        """Persist a provider change; cascade model + voice to their defaults.

        The cascade rule: setting a provider writes provider +
        ``MODEL_TABLE.available(provider)[0]`` (empty string for modelless
        providers) + ``roster[0]`` for the voice. All three land in one
        atomic write so a reader sees them together, never a partial
        state. A re-publish of the same provider neither rewrites the disk
        nor refetches the roster; it clears a stale notice and answers
        :meth:`_cleared_notice`'s verdict on whether that needs a push.

        Roster fetch happens OUTSIDE the lock -- it is a voxd round-trip
        that must not block other panel threads on their own reads. The
        provider is re-checked under the lock after the fetch: if another
        thread swapped the provider mid-flight (e.g. a second click hit
        first), this call gives up rather than clobber the newer state.
        That re-check is the *only* one the post-fetch section needs: the
        pre-fetch guard above already returned on ``provider ==
        pre_fetch_provider``, so once the re-check confirms ``_state``
        still holds ``pre_fetch_provider``, ``provider`` differs from it
        by construction and the write below always has work to do.

        A ``VoxdConnectionError`` on the roster fetch surfaces as a
        transient notice; the write is abandoned so the disk never lands
        a provider whose voice roster we could not read. That abandonment,
        and the mid-fetch-race abort below, both answer
        :attr:`~ControlPush.CORRECT` to snap back the optimistic widget.
        """
        with self._lock:
            pre_fetch_provider = self._state.provider
            if provider == pre_fetch_provider:
                return self._cleared_notice()
        target = Cascade.default_target(provider)
        roster = self._fetch_roster_or_notice(target, "provider-switch")
        if isinstance(roster, RosterError):
            return ControlPush.CORRECT
        voice_default = Cascade.first_or_empty(roster)
        with self._lock:
            # Re-check under the lock: another thread may have committed a
            # different provider while our roster RPC was in flight. If the
            # provider we saw before the fetch is not the one we see now,
            # give up rather than overwrite that newer state.
            if self._state.provider != pre_fetch_provider:
                logger.info(
                    "vox-panel: provider changed to %r mid-fetch; "
                    "aborting our %r commit",
                    self._state.provider,
                    provider,
                )
                # Whatever the band is showing was put there by whoever
                # authored it, most likely the thread whose commit won this
                # race. This one changed nothing and has nothing to report,
                # so it leaves the notice alone rather than destroying
                # another thread's reason before anyone reads it.
                return ControlPush.CORRECT
            self._store.write_fields(
                {
                    "provider": provider,
                    "model": target.model_field,
                    "voice": voice_default,
                }
            )
            self._state = self._state.with_provider(
                provider,
                roster=roster,
                model=target.model,
                voice=voice_default or None,
            )
            self._notice = PanelNotice.silent()
            return ControlPush.REFRESH

    def commit_model(self, model: str) -> ControlPush:
        """Persist a model change; cascade voice to the current-provider default.

        The cascade rule: setting a model writes model +
        the first voice for that model, in one
        atomic write. A ``VoxdConnectionError`` on the roster fetch
        surfaces as a transient notice; the write is abandoned so the
        disk never lands a model whose companion voice we could not read.

        Roster fetch happens OUTSIDE the lock -- it is a voxd round-trip
        that must not block other panel threads on their own reads. The
        provider and model are re-checked under the lock after the fetch
        so a concurrent switch can't be overwritten by a stale result.

        Every abandoned commit below -- no provider selected yet, an
        unreadable roster, a provider changed mid-fetch -- answers
        :attr:`~ControlPush.CORRECT` for the same reason
        ``commit_provider`` does: the widget already applied the pick and
        ``_state`` never moved, so only a full reinstall corrects it.
        """
        before = self.snapshot()
        pre_fetch_provider = before.provider
        if pre_fetch_provider is None:
            # A provider-less panel renders no clickable model at all, so a
            # MODEL event cannot originate from the widget in this state.
            # What can reach here is a race: a concurrent resync clearing
            # the provider between the click's own snapshot and this lock.
            # Skip rather than fetch a cascaded voice roster from a
            # provider the session no longer declares. The notice is left
            # as it stands -- this call has nothing of its own to report.
            logger.info("vox-panel: model click ignored; no provider selected yet")
            return ControlPush.CORRECT
        target = VoiceTarget(pre_fetch_provider, model)
        roster = self._fetch_roster_or_notice(target, "model-switch")
        if isinstance(roster, RosterError):
            return ControlPush.CORRECT
        voice_default = Cascade.first_or_empty(roster)
        with self._lock:
            if (self._state.provider, self._state.model) != (
                before.provider,
                before.model,
            ):
                return ControlPush.CORRECT
            self._store.write_fields({"model": model, "voice": voice_default})
            self._state = self._state.with_provider(
                pre_fetch_provider,
                roster=roster,
                model=model,
                voice=voice_default or None,
            )
            self._notice = PanelNotice.silent()
            return ControlPush.REFRESH

    def _cleared_notice(self) -> ControlPush:
        """Drop any displayed notice; answer the push that drop needs.

        Callers hold ``_lock``, which is not reentrant, so this never takes
        it. That is a contract the type system cannot state: reading
        ``is_present`` and writing ``_notice`` is a read-modify-write, and a
        caller that arrives without the lock does not deadlock loudly -- it
        races another thread's notice and loses one silently.

        Only an already-silent panel is a true no-op: clearing a visible
        notice changes the scene and needs a refresh.
        """
        if not self._notice.is_present:
            return ControlPush.NONE
        self._notice = PanelNotice.silent()
        return ControlPush.REFRESH
