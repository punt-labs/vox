"""The world a lifecycle verb observes: the service manager and the port.

:class:`LifecycleSystem` bundles what every verb needs -- the backend, the
probe, the poll, and the installed wheel's version -- and takes the
observation a verb decides on. Observing runs the port probe first, so a
broken probe fails the verb before any row is consulted.
"""

from __future__ import annotations

import os
import platform
from dataclasses import dataclass
from typing import TYPE_CHECKING, Self, final

from punt_vox.paths import installed_version, log_dir
from punt_vox.service.clock import Poll, SystemClock
from punt_vox.service.daemon_probe import DaemonProbe
from punt_vox.service.installer import ServiceInstaller
from punt_vox.service.lifecycle_report import (
    LifecycleOutcome,
    LifecycleRefusedError,
    LifecycleReport,
)
from punt_vox.service.process import DEFAULT_PORT

if TYPE_CHECKING:
    from punt_vox.service.backend import (
        LifecycleProbe,
        ProcessHandle,
        ServiceBackend,
    )
    from punt_vox.service.lifecycle_report import LifecycleVerb
    from punt_vox.service.types import PlatformName

__all__ = ["LifecycleSystem", "Observation"]


@final
@dataclass(frozen=True, slots=True)
class Observation:
    """What a verb sees before it acts."""

    installed: bool
    active: bool
    mainpid: int | None  # None: the service manager reports no main process
    procs: frozenset[ProcessHandle]
    listening: bool

    @property
    def quiet(self) -> bool:
        """Return True when nothing runs: no unit, no voxd, no listener."""
        return not self.active and not self.procs and not self.listening

    def holder_pids(self) -> str:
        """Return the visible voxd pids on the port, comma-separated."""
        return ", ".join(str(pid) for pid in sorted(p.pid for p in self.procs))


@final
class LifecycleSystem:
    """The backend, probe and poll one lifecycle run drives."""

    __slots__ = ("_backend", "_log_path", "_poll", "_probe", "_version")

    _backend: ServiceBackend
    _probe: LifecycleProbe
    _poll: Poll
    _version: str
    _log_path: str

    def __new__(
        cls,
        backend: ServiceBackend,
        probe: LifecycleProbe,
        poll: Poll,
        version: str,
    ) -> Self:
        self = super().__new__(cls)
        self._backend = backend
        self._probe = probe
        self._poll = poll
        self._version = version
        self._log_path = str(log_dir() / "vox.log")
        return self

    @classmethod
    def for_this_host(cls) -> Self:
        """Build the real system, refusing root and unsupported platforms.

        The refusal happens before any backend is built, so a refused verb
        never touches the service manager.
        """
        plat = cls._checked_platform()
        backend = ServiceInstaller.backend_for(plat)
        probe = DaemonProbe.for_bind(backend.installed_bind())
        return cls(backend, probe, Poll(SystemClock()), installed_version())

    @staticmethod
    def _checked_platform() -> PlatformName:
        """Return the platform, or raise ``LifecycleRefusedError``."""
        if os.geteuid() == 0:
            msg = (
                "vox daemon must run as your normal user, not root or sudo: the "
                "service and its state live in your home directory. Re-run "
                "without sudo."
            )
            raise LifecycleRefusedError(msg)
        system = platform.system()
        if system == "Darwin":
            return "macos"
        if system == "Linux":
            return "linux"
        msg = f"vox daemon supports macOS and Linux, not {system}"
        raise LifecycleRefusedError(msg)

    @property
    def backend(self) -> ServiceBackend:
        """Return the service manager seam."""
        return self._backend

    @property
    def probe(self) -> LifecycleProbe:
        """Return the port and process probe."""
        return self._probe

    @property
    def poll(self) -> Poll:
        """Return the bounded poll every wait shares."""
        return self._poll

    @property
    def version(self) -> str:
        """Return the installed wheel's version."""
        return self._version

    def is_active(self) -> bool:
        """Return True while the manager runs, starts, or stops the job.

        A unit whose manager still reports a main process has not stopped,
        whatever state it is in; counting it as active keeps every row from
        reading a stopping unit as stopped.
        """
        return self._backend.is_active() or self._backend.main_pid() is not None

    def observe(self) -> Observation:
        """Observe the port first, then the service manager.

        Raises ``PortProbeError`` when the port cannot be inspected.
        """
        procs = self._probe.voxd_processes()
        listening = self._probe.port_listening()
        mainpid = self._backend.main_pid()
        return Observation(
            installed=self._backend.is_installed(),
            active=self._backend.is_active() or mainpid is not None,
            mainpid=mainpid,
            procs=procs,
            listening=listening,
        )

    def report(
        self, verb: LifecycleVerb, outcome: LifecycleOutcome, detail: str
    ) -> LifecycleReport:
        """Return a report for *verb* on the service port."""
        return LifecycleReport(
            verb=verb,
            outcome=outcome,
            port=DEFAULT_PORT,
            log_path=self._log_path,
            detail=detail,
        )
