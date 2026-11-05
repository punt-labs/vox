"""What a ``vox daemon stop|start|restart`` verb reports, and how it exits.

The outcome set is closed: every path through the lifecycle ends in exactly
one :class:`LifecycleOutcome`, and its ``ok`` flag alone decides the exit code.
Expected failures come back as a report; a precondition refusal (root, an
unsupported OS) raises :class:`LifecycleRefusedError` before anything is observed.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TYPE_CHECKING, Literal, Self, TypedDict, final

if TYPE_CHECKING:
    from punt_vox.types_health import HealthStatus

__all__ = [
    "LifecycleOutcome",
    "LifecycleRefusedError",
    "LifecycleReport",
    "LifecycleReportJson",
    "LifecycleVerb",
]

type LifecycleVerb = Literal["stop", "start", "restart"]


@final
class LifecycleOutcome(StrEnum):
    """The closed set of verb outcomes; each knows whether it succeeded."""

    STOPPED = "stopped"
    ALREADY_STOPPED = "already_stopped"
    STARTED = "started"
    ALREADY_RUNNING = "already_running"
    RESTARTED = "restarted"
    NOT_INSTALLED = "not_installed"
    PERMISSION_DENIED = "permission_denied"
    BACKEND_FAILED = "backend_failed"
    TIMEOUT = "timeout"
    PORT_CONFLICT = "port_conflict"
    VERSION_MISMATCH = "version_mismatch"

    @property
    def ok(self) -> bool:
        """Return True for the five outcomes in which the verb did its job."""
        return self in _OK_OUTCOMES

    @property
    def exit_code(self) -> int:
        """Return 0 for a successful outcome and 1 for every failure."""
        return 0 if self.ok else 1


_OK_OUTCOMES = frozenset(
    {
        LifecycleOutcome.STOPPED,
        LifecycleOutcome.ALREADY_STOPPED,
        LifecycleOutcome.STARTED,
        LifecycleOutcome.ALREADY_RUNNING,
        LifecycleOutcome.RESTARTED,
    }
)


class LifecycleReportJson(TypedDict):
    """The ``--json`` shape of one lifecycle report."""

    verb: str
    outcome: str
    ok: bool
    pid: int | None  # None: no voxd process is running after the verb
    previous_pid: int | None  # None: no voxd process was running before it
    port: int
    daemon_version: str | None  # None: no daemon answered health
    detail: str
    log_path: str


@final
class LifecycleRefusedError(ValueError):
    """The verb refuses to run here: as root, or on an unsupported OS."""

    __slots__ = ()


@final
@dataclass(frozen=True, slots=True)
class LifecycleReport:
    """One verb's result: the outcome, the processes it names, and why."""

    verb: LifecycleVerb
    outcome: LifecycleOutcome
    port: int
    log_path: str
    detail: str = ""
    pid: int | None = None  # None: no voxd process is running after the verb
    previous_pid: int | None = None  # None: none was running before it
    daemon_version: str | None = None  # None: no daemon answered health

    @property
    def exit_code(self) -> int:
        """Return the process exit code this report maps to."""
        return self.outcome.exit_code

    def as_verb(self, verb: LifecycleVerb) -> Self:
        """Return this report relabelled as *verb*'s, its detail prefixed.

        ``restart`` reports a failed stop or start half as its own outcome; the
        prefix names which half failed.
        """
        return replace(self, verb=verb, detail=f"{self.verb}: {self.detail}")

    def as_outcome(self, outcome: LifecycleOutcome) -> Self:
        """Return this report with *outcome* in place of its own."""
        return replace(self, outcome=outcome)

    def naming(self, *, previous_pid: int | None) -> Self:
        """Return this report naming the voxd process that ran before the verb.

        ``None`` is the documented value when nothing ran before it.
        """
        return replace(self, previous_pid=previous_pid)

    def serving(self, status: HealthStatus) -> Self:
        """Return this report naming the voxd process that answered health."""
        return replace(self, pid=status.pid, daemon_version=status.daemon_version)

    def restarted_after(self, stop: LifecycleReport) -> Self:
        """Return this start report as ``restart``'s, naming *stop*'s process."""
        return replace(
            self,
            verb="restart",
            outcome=LifecycleOutcome.RESTARTED,
            previous_pid=stop.previous_pid,
        )

    def to_json(self) -> LifecycleReportJson:
        """Return the report as the ``--json`` payload."""
        return {
            "verb": self.verb,
            "outcome": self.outcome.value,
            "ok": self.outcome.ok,
            "pid": self.pid,
            "previous_pid": self.previous_pid,
            "port": self.port,
            "daemon_version": self.daemon_version,
            "detail": self.detail,
            "log_path": self.log_path,
        }

    def text(self) -> str:
        """Return the one-line human summary, plus the detail and log hint."""
        lines = [self._headline()]
        if self.detail:
            lines.append(f"  {self.detail}")
        if not self.outcome.ok:
            lines.append(f"  Check the logs at {self.log_path}")
        return "\n".join(lines)

    def _headline(self) -> str:
        """Return the summary line for this outcome."""
        was = f" (was pid {self.previous_pid})" if self.previous_pid else ""
        running = f"pid {self.pid}, port {self.port}, version {self.daemon_version}"
        match self.outcome:
            case LifecycleOutcome.STOPPED:
                return f"voxd stopped{was}"
            case LifecycleOutcome.ALREADY_STOPPED:
                return "voxd already stopped"
            case LifecycleOutcome.STARTED:
                return f"voxd started ({running})"
            case LifecycleOutcome.ALREADY_RUNNING:
                return f"voxd already running ({running})"
            case LifecycleOutcome.RESTARTED:
                return f"voxd restarted ({running}){was}"
            case _:
                return f"vox daemon {self.verb} failed: {self.outcome.value}"
