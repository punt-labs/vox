"""The service-manager seam the daemon lifecycle drives, and its typed errors.

A types module: the Protocols here are the contracts ``DaemonLifecycle``
depends on, and the exceptions are the failures it classifies. Nothing here
runs a command or touches a process, so importing it pulls in no backend.

Every failure a backend command can produce surfaces as one of three typed
errors. The lifecycle never reads a command's exit code as the outcome by
itself: after any of these it observes the service again and carries on when
the postcondition already holds.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, Self, final

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from punt_vox.types_health import HealthStatus

__all__ = [
    "KILL_GRACE_FLOOR_S",
    "BackendCommandError",
    "Clock",
    "CommandRunner",
    "InstallableBackend",
    "LifecycleProbe",
    "PortProbeError",
    "PrivilegeDeniedError",
    "ProcessHandle",
    "ServiceBackend",
    "TerminalCheck",
]


class ServiceBackend(Protocol):
    """What the lifecycle asks of a service manager (systemd or launchd)."""

    def is_installed(self) -> bool:
        """Return True when the unit file or plist is present."""
        ...

    def is_active(self) -> bool:
        """Return True while the manager runs, starts, or is stopping the job."""
        ...

    def main_pid(self) -> int | None:
        """Return the manager's main process id.

        ``None`` is the documented answer when the manager reports no process.
        """
        ...

    def installed_bind(self) -> str:
        """Return the ``VOXD_BIND`` written into the unit; ``""`` when none."""
        ...

    def request_stop(self) -> None:
        """Ask the manager to stop the job; raise a typed error on failure."""
        ...

    def request_start(self) -> None:
        """Ask the manager to start the job; raise a typed error on failure."""
        ...


class InstallableBackend(ServiceBackend, Protocol):
    """A service backend that can also write and remove its unit."""

    @property
    def service_path(self) -> str:
        """Return the unit file or plist path this backend installs."""
        ...

    def is_active_within(self, within_s: float) -> bool:
        """Return True when the manager confirms the job active in *within_s*.

        The query takes at most *within_s*, for a bounded wait; one that runs
        out of time confirms nothing and returns False. A failed query raises.
        """
        ...

    def install(self, user: str) -> None:
        """Write the unit for *user* and bring the job up."""
        ...

    def uninstall(self) -> None:
        """Bring the job down and remove the unit."""
        ...


KILL_GRACE_FLOOR_S = 1.0
"""The shortest SIGTERM grace worth a SIGKILL after it.

A ``terminate()`` budget below this sends SIGTERM only: SIGKILL is never sent
without a real chance to exit cleanly first.
"""


class ProcessHandle(Protocol):
    """A handle pinned to one voxd process, never to a bare, reusable pid."""

    @property
    def pid(self) -> int:
        """Return the process id the handle was opened on."""
        ...

    def exited(self) -> bool:
        """Return True once the pinned process has exited."""
        ...

    def terminate(self, within_s: float) -> bool:
        """SIGTERM, then SIGKILL after a grace; return whether it exited.

        Takes at most *within_s* seconds, so a caller's deadline holds. Below
        ``KILL_GRACE_FLOOR_S`` it sends SIGTERM only.
        """
        ...


class LifecycleProbe(Protocol):
    """What the lifecycle observes about voxd on its port."""

    def voxd_processes(self) -> frozenset[ProcessHandle]:
        """Return handles on the visible voxd processes holding the port."""
        ...

    def off_port_voxd(self) -> frozenset[int]:
        """Return pids of voxd processes on a port other than the service's."""
        ...

    def open_handle(self, pid: int) -> ProcessHandle | None:
        """Return a handle on *pid*.

        ``None`` is the documented answer when *pid* is gone or is not voxd.
        """
        ...

    def service_owned(self, pid: int) -> bool:
        """Return True when the service manager still counts *pid* as its own."""
        ...

    def clear_port_file(self) -> None:
        """Remove a stale ``serve.port`` naming the service port.

        Left alone while a visible voxd holds the port; an unparseable file is
        stale and removed. Raises ``PortProbeError`` when it cannot be removed.
        """
        ...

    def port_listening(self) -> bool:
        """Return True when anything accepts a TCP connection on the port."""
        ...

    def health(self, holders: frozenset[ProcessHandle]) -> HealthStatus | None:
        """Return voxd's health, sent only when *holders* is non-empty.

        ``None`` is the documented answer for "no health reply".
        """
        ...

    def health_miss(self) -> Exception | None:
        """Return why the last health request got no answer.

        ``None`` is the documented answer when no request has failed.
        """
        ...


class Clock(Protocol):
    """Monotonic time and sleep, so a poll can run on a fake clock."""

    def monotonic(self) -> float:
        """Return seconds on a monotonic clock."""
        ...

    def sleep(self, seconds: float) -> None:
        """Block for *seconds*."""
        ...


class CommandRunner(Protocol):
    """Run one argv and report its exit code and captured stderr."""

    def __call__(self, argv: Sequence[str], *, capture: bool) -> tuple[int, str]:
        """Run *argv*; capture stderr when *capture*, else let it reach the tty."""
        ...


type TerminalCheck = Callable[[], bool]
"""Answer whether the process has a controlling terminal to prompt on."""


@final
class BackendCommandError(RuntimeError):
    """A service-manager command failed: its argv, exit code and stderr tail.

    Built through :meth:`of`, so the message is rendered once and the command
    and exit code stay available to the caller that classifies the failure.
    """

    __slots__ = ("_argv", "_code")

    _argv: tuple[str, ...]
    _code: int

    @classmethod
    def of(cls, argv: Sequence[str], code: int, detail: str) -> Self:
        """Return the error for *argv* exiting *code*, with *detail* appended."""
        self = cls(f"{' '.join(argv)} exited {code}: {detail}")
        self._argv = tuple(argv)
        self._code = code
        return self

    @property
    def code(self) -> int:
        """Return the command's exit code."""
        return self._code

    @property
    def argv(self) -> tuple[str, ...]:
        """Return the command that failed."""
        return self._argv


@final
class PrivilegeDeniedError(PermissionError):
    """The command needed a privilege vox could not get without a prompt."""

    __slots__ = ()


@final
class PortProbeError(RuntimeError):
    """``fuser``, ``lsof`` or ``ps`` was missing, failed, or timed out.

    A broken probe never reads as "nothing on the port".
    """

    __slots__ = ()
