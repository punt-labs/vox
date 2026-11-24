"""Reconcile the panel's confirmed settings with disk and the daemon."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Self, final

from punt_vox.client_errors import VoxdConnectionError, VoxdRejectionError
from punt_vox.panel.panel_notice import PanelNotice
from punt_vox.panel.state import PanelState

if TYPE_CHECKING:
    from collections.abc import Callable

    from punt_vox.panel.panel_settings import PanelSettings
    from punt_vox.panel.ports import PanelDaemonClient, SettingsStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RefreshNotices:
    """Notice policy for normal refresh or write-failure recovery."""

    success: PanelNotice
    unavailable: PanelNotice
    rejected: Callable[[str], PanelNotice]

    @classmethod
    def write_failure(cls, field: str) -> Self:
        """Keep the write failure visible alongside any daemon failure."""
        return cls(
            PanelNotice.write_failed(field),
            PanelNotice.write_failed_and_voxd_unavailable(field),
            lambda detail: PanelNotice.write_failed_and_voxd_rejected(field, detail),
        )


_NORMAL_NOTICES = RefreshNotices(
    PanelNotice.silent(),
    PanelNotice.voxd_unavailable(),
    PanelNotice.voxd_rejected,
)


@final
class SettingsRefresh:
    """Read a snapshot and install it only while its predecessor is current."""

    __slots__ = ("_client", "_store")
    _client: PanelDaemonClient
    _store: SettingsStore

    def __new__(cls, client: PanelDaemonClient, store: SettingsStore) -> Self:
        """Bind the disk and roster sources used for each refresh."""
        self = super().__new__(cls)
        self._client = client
        self._store = store
        return self

    def refresh(
        self,
        settings: PanelSettings,
        notices: RefreshNotices = _NORMAL_NOTICES,
        *,
        external: bool = False,
    ) -> bool:
        """Read and compare-and-replace a snapshot using the caller's notices."""
        before = settings.snapshot()
        fresh, notice, failure = self._read(before, notices, external=external)
        changed = settings.replace_if_current(
            before,
            fresh,
            notice,
            refresh_notice=external
            or (failure is not None and not notices.success.is_present),
        )
        if changed and failure is not None:
            logger.warning(
                "vox-panel: resync failed: %s", notice.message, exc_info=failure
            )
        return changed

    def _read(
        self, before: PanelState, notices: RefreshNotices, *, external: bool
    ) -> tuple[PanelState, PanelNotice, Exception | None]:
        fresh = before
        try:
            fresh = PanelState.read(self._store)
            state = fresh.with_roster(
                self._client, previous=before if external else None
            )
        except (OSError, UnicodeDecodeError) as exc:
            notice = (
                notices.success
                if notices.success.is_present
                else PanelNotice.config_unreadable()
            )
            return before, notice, exc
        except VoxdRejectionError as exc:
            return (
                self._after_refusal(before, fresh, external=external),
                notices.rejected(str(exc)),
                exc,
            )
        except VoxdConnectionError as exc:
            return before, notices.unavailable, exc
        return state, notices.success, None

    @staticmethod
    def _after_refusal(
        before: PanelState, fresh: PanelState, *, external: bool
    ) -> PanelState:
        """Keep the confirmed picker unless the on-disk model needs repair."""
        if not external or (
            fresh.provider == before.provider and fresh.model != before.model
        ):
            return fresh
        return before
