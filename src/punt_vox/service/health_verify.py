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
from punt_vox.service.voxd_bind import VoxdBind

if TYPE_CHECKING:
    from collections.abc import Mapping

    from punt_vox.service.backend import InstallableBackend
    from punt_vox.service.query_answer import QueryAnswer
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
    """Wait for a freshly installed unit to run and serve, or fail with why."""

    __slots__ = ("_backend", "_probe")

    _backend: InstallableBackend
    _probe: DaemonProbe

    def __new__(cls, backend: InstallableBackend, probe: DaemonProbe) -> Self:
        # *backend* reports whether the unit runs and names the unit file the
        # failure message points at; *probe* targets the daemon the backend
        # runs (its installed bind, the service port, the run-file token).
        self = super().__new__(cls)
        self._backend = backend
        self._probe = probe
        return self

    @classmethod
    def for_backend(cls, backend: InstallableBackend) -> Self:
        """Return the verifier for the daemon *backend* just installed."""
        return cls(backend, DaemonProbe.for_backend(backend))

    @staticmethod
    def preflight(environ: Mapping[str, str]) -> None:
        """Refuse, before install writes anything, a unit health cannot poll.

        The unit carries ``VOXD_BIND`` from *environ* verbatim, and health
        polls only an IP address; a name raises ``PortProbeError`` here rather
        than after keys, directories, and the unit are written.
        """
        VoxdBind.from_environ(environ)

    def verify(self) -> HealthStatus:
        """Wait for the unit to be active and voxd to answer; return its health.

        Without this, ``install()`` reports success for a unit that never ran
        or a daemon that died on startup -- the silent-down failure mode.
        Raise ``ServiceHealthError``, chained to the last health failure, so
        the caller can surface a non-zero exit (or degrade gracefully).
        """
        # Each is-active query gets only what is left of the wait, so a slow
        # systemctl/launchctl cannot carry install past its bound.
        active = self._probe.poll.answer_within(
            self._backend.is_active_within, _HEALTH_DEADLINE_S
        )
        if not active.holds:
            raise ServiceHealthError(self._inactive(active))
        status = self._probe.wait_up(_HEALTH_DEADLINE_S)
        if status is not None:
            return status
        miss = self._probe.health_miss()
        raise ServiceHealthError(self._unreachable(miss)) from miss

    def _inactive(self, answer: QueryAnswer) -> str:
        """Return the never-active message: a hung manager, or a unit not running."""
        service = self._backend.service_path
        wait = f"{_HEALTH_DEADLINE_S:.0f}s"
        if answer.timed_out:
            return (
                f"{answer.unanswered} did not answer within the {wait} wait after "
                "install: the service manager is not responding, so whether voxd "
                f"runs is unknown. Service: {service}."
            )
        if answer.stalled:
            seen = (
                f"voxd was last seen not active; then {answer.stalled} stopped "
                f"answering before the {wait} wait ended"
            )
        else:
            seen = f"voxd is not active {wait} after install"
        return (
            f"{seen}. Service: {service}. Check the daemon logs in {_paths_log_dir()}."
        )

    def _unreachable(self, miss: Exception | None) -> str:
        """Return the never-reachable message, naming the last health failure."""
        target = self._probe.target
        cause = "" if miss is None else f" (last error: {miss})"
        return (
            f"voxd registered but never became reachable within "
            f"{_HEALTH_DEADLINE_S:.0f}s on {target.host}:{target.port}{cause}. "
            f"Service: {self._backend.service_path}. Check the daemon logs in "
            f"{_paths_log_dir()}."
        )
