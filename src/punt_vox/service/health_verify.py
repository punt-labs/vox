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
from typing import TYPE_CHECKING, Self, final

from punt_vox.paths import log_dir as _paths_log_dir
from punt_vox.service.daemon_probe import DaemonProbe

if TYPE_CHECKING:
    from punt_vox.service.backend import InstallableBackend
    from punt_vox.types_health import HealthStatus

logger = logging.getLogger(__name__)

__all__ = ["HealthVerifier", "ServiceHealthError"]

# voxd health poll after install. launchctl/systemctl registration proves the
# job is scheduled, not that voxd bound its port and stayed up; a daemon that
# dies on startup (bad env, missing binary) would otherwise be reported
# "running". Poll the health endpoint until it answers or the deadline lapses.
_HEALTH_DEADLINE_S = 5.0


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

    __slots__ = ("_probe", "_service_path")

    _probe: DaemonProbe
    _service_path: str

    def __new__(cls, probe: DaemonProbe, service_path: str) -> Self:
        # *probe* targets the daemon the backend runs (its installed bind, the
        # service port, the run-file token); *service_path* names the plist or
        # unit the failure message points the operator at.
        self = super().__new__(cls)
        self._probe = probe
        self._service_path = service_path
        return self

    @classmethod
    def for_backend(cls, backend: InstallableBackend) -> Self:
        """Return the verifier for the daemon *backend* just installed."""
        probe = DaemonProbe.for_bind(backend.installed_bind())
        return cls(probe, backend.service_path)

    def verify(self) -> HealthStatus:
        """Poll voxd's gated health until it answers; return what it reported.

        Without this poll, ``install()`` reports "running" for a daemon that
        died on startup -- the silent-down failure mode. Raise
        ``ServiceHealthError`` so the caller can surface a non-zero exit (or
        degrade gracefully) when voxd never becomes reachable.
        """
        status = self._probe.wait_up(_HEALTH_DEADLINE_S)
        if status is not None:
            return status
        target = self._probe.target
        msg = (
            f"voxd registered but never became reachable within "
            f"{_HEALTH_DEADLINE_S:.0f}s on {target.host}:{target.port}. "
            f"Service: {self._service_path}. Check the daemon logs in "
            f"{_paths_log_dir()}."
        )
        raise ServiceHealthError(msg)
