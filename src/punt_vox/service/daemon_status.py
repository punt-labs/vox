"""What ``vox daemon status`` reports about voxd, and how it exits.

The state set is closed. Only an answer from voxd is ``running``. A visible
voxd that did not answer is ``unresponsive``, never "not running": the process
is there. A port vox cannot attribute or inspect is ``unknown``. Only a port
with no voxd and no listener is ``not_running``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Self, final

if TYPE_CHECKING:
    from collections.abc import Iterable

    from punt_vox.types_health import HealthStatus

__all__ = ["DaemonState", "DaemonStatus"]


@final
class DaemonState(StrEnum):
    """The closed set of answers ``vox daemon status`` gives."""

    RUNNING = "running"
    UNRESPONSIVE = "unresponsive"
    UNKNOWN = "unknown"
    NOT_RUNNING = "not_running"


@final
@dataclass(frozen=True, slots=True)
class DaemonStatus:
    """One reading of voxd: its state, why, and the processes it names."""

    state: DaemonState
    reason: str = ""
    health: HealthStatus | None = None  # None: no voxd answered health
    pids: tuple[int, ...] = ()  # the visible voxd holders that did not answer
    off_port: tuple[int, ...] = ()  # voxd pids on the port serve.port names

    @classmethod
    def running(cls, health: HealthStatus) -> Self:
        """Return the reading for a voxd that answered with *health*."""
        return cls(DaemonState.RUNNING, health=health)

    @classmethod
    def unresponsive(cls, pids: Iterable[int], why: str) -> Self:
        """Return the reading for visible voxd *pids* that did not answer."""
        held = tuple(sorted(pids))
        named = ", ".join(str(pid) for pid in held)
        reason = f"voxd pid {named} holds the port but did not answer health: {why}"
        return cls(DaemonState.UNRESPONSIVE, reason=reason, pids=held)

    @classmethod
    def unknown(cls, reason: str) -> Self:
        """Return the reading when vox cannot tell whether voxd runs."""
        return cls(DaemonState.UNKNOWN, reason=reason)

    @classmethod
    def not_running(cls, reason: str, off_port: Iterable[int] = ()) -> Self:
        """Return the reading for no voxd, naming any voxd on another port."""
        others = tuple(sorted(off_port))
        if others:
            named = ", ".join(str(pid) for pid in others)
            reason += f"; voxd pid {named} on another port was not counted"
        return cls(DaemonState.NOT_RUNNING, reason=reason, off_port=others)

    @property
    def exit_code(self) -> int:
        """Return 0 only when voxd answered."""
        return 0 if self.state is DaemonState.RUNNING else 1

    def to_json(self) -> dict[str, object]:  # wire boundary: json.dumps input
        """Return the ``--json`` payload: the health snapshot, else the reason."""
        if self.health is not None:
            return self._snapshot(self.health)
        payload: dict[str, object] = {"status": self.state.value, "reason": self.reason}
        if self.pids:
            payload["pids"] = list(self.pids)
        if self.off_port:
            payload["off_port_pids"] = list(self.off_port)
        return payload

    def text(self) -> str:
        """Return the human summary."""
        health = self.health
        if health is None:
            return f"Daemon: {self.state.value.replace('_', ' ')} ({self.reason})"
        return (
            f"Daemon: {health.status} on port {health.port}\n"
            f"  Uptime:   {health.uptime_seconds}s\n"
            f"  Sessions: {health.active_sessions}"
        )

    @staticmethod
    def _snapshot(health: HealthStatus) -> dict[str, object]:
        """Return the fields the daemon reports over ``health()``.

        ``provider`` is not here: the daemon has no provider of its own;
        per-provider readiness is the ``provider_status`` op.
        """
        return {
            "status": health.status,
            "port": health.port,
            "pid": health.pid,
            "daemon_version": health.daemon_version,
            "uptime_seconds": health.uptime_seconds,
            "queued": health.queued,
            "active_sessions": health.active_sessions,
        }
