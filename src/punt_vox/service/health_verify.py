"""Post-install health verification: poll voxd until it serves or fail loud.

``launchctl``/``systemctl`` registration proves only that the job is
scheduled, not that voxd bound its port and stayed up. This module owns the
post-install poll that closes that gap and the typed error it raises so the
two callers can diverge: ``vox daemon install`` lets ``ServiceHealthError``
propagate (loud non-zero exit), while the best-effort marketplace ``vox
install`` catches it and degrades to a skip.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Self, final

from punt_vox.client_errors import VoxdConnectionError, VoxdProtocolError
from punt_vox.paths import log_dir as _paths_log_dir
from punt_vox.service.health import HealthTarget

if TYPE_CHECKING:
    from pathlib import Path

    from punt_vox.service.types import PlatformName

logger = logging.getLogger(__name__)

__all__ = ["HealthVerifier", "ServiceHealthError"]

# voxd health poll after install. launchctl/systemctl registration proves the
# job is scheduled, not that voxd bound its port and stayed up; a daemon that
# dies on startup (bad env, missing binary) would otherwise be reported
# "running". Poll the health endpoint until it answers or the deadline lapses.
_HEALTH_DEADLINE_S = 5.0
_HEALTH_POLL_INTERVAL_S = 0.2


@final
class ServiceHealthError(RuntimeError):
    """voxd registered with the service manager but never answered health.

    Raised when ``launchctl``/``systemctl`` accepts the job yet the health
    poll exhausts its deadline -- the silent-down failure mode (bad env, a
    broken ``voxd`` binary, port contention). A ``RuntimeError`` subtype so it
    reads as an operational failure, but a *distinct* type so the caller can
    catch it precisely: ``vox daemon install`` lets it propagate (loud exit),
    the best-effort marketplace ``vox install`` catches it and skips. The
    sibling ``LaunchctlError`` is the bring-up analogue; this is its serve-time
    counterpart.
    """

    __slots__ = ()


@final
class HealthVerifier:
    """Poll a freshly installed voxd until it serves or the deadline lapses."""

    __slots__ = ("_service_path", "_target")

    _service_path: Path
    _target: HealthTarget

    def __new__(cls, platform: PlatformName, service_path: Path) -> Self:
        # *platform* selects the backend whose VOXD_BIND gating the health host
        # mirrors (see ``HealthTarget._effective_bind``); *service_path* names
        # the plist/unit the failure message points the operator at.
        self = super().__new__(cls)
        self._target = HealthTarget(platform)
        self._service_path = service_path
        return self

    def verify(self) -> None:
        """Poll voxd's health endpoint until it answers or the deadline lapses.

        Without this poll, ``install()`` reports "running" for a daemon that
        died on startup -- the silent-down failure mode. Raise
        ``ServiceHealthError`` so the caller can surface a non-zero exit (or
        degrade gracefully) when voxd never becomes reachable.
        """
        target = self._target
        deadline = time.monotonic() + _HEALTH_DEADLINE_S
        # None until the first probe fails: no exception has occurred yet.
        last_exc: VoxdConnectionError | VoxdProtocolError | OSError | None = None
        while time.monotonic() < deadline:
            try:
                target.client().health()
                return
            except (VoxdConnectionError, VoxdProtocolError, OSError) as exc:
                # VoxdProtocolError covers a receive timeout while voxd is
                # still binding its port -- transient during startup, so
                # retry until the deadline rather than failing on the first.
                last_exc = exc
                time.sleep(_HEALTH_POLL_INTERVAL_S)
        log_dir = _paths_log_dir()
        msg = (
            f"voxd registered but never became reachable within "
            f"{_HEALTH_DEADLINE_S:.0f}s on {target.host}:{target.port}. "
            f"Service: {self._service_path}. Check the daemon logs in {log_dir}."
        )
        raise ServiceHealthError(msg) from last_exc
