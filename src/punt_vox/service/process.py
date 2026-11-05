"""Process management for the voxd daemon."""

from __future__ import annotations

import logging
import os
import platform
import re
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Self

from punt_vox.paths import run_dir as _user_run_dir
from punt_vox.service.backend import PortProbeError
from punt_vox.service.daemon_process import DaemonProcess
from punt_vox.service.deadline import Deadline
from punt_vox.service.process_identity import ProcessIdentity
from punt_vox.service.stale_port_file import StalePortFile
from punt_vox.service.termination import Termination

if TYPE_CHECKING:
    from punt_vox.service.backend import ProcessHandle
    from punt_vox.service.port_file_cleanup import PortFileCleanup

logger = logging.getLogger(__name__)

_SUBPROCESS_TIMEOUT_SECONDS = 5

DEFAULT_PORT = 8421


class ProcessManager:
    """Manage voxd daemon processes: find, verify, kill, and port-file I/O."""

    __slots__ = ("_deadline", "_note")

    # SIGTERM grace plus the SIGKILL wait for an install/uninstall kill.
    _KILL_BUDGET_S: ClassVar[float] = 7.0

    _deadline: Deadline
    _note: str  # "" unless the last kill left serve.port behind

    def __new__(cls) -> Self:
        self = super().__new__(cls)
        self._deadline = Deadline.never()
        self._note = ""
        return self

    @property
    def port_file_note(self) -> str:
        """Return why the last confirmed kill left ``serve.port`` behind; ``""``."""
        return self._note

    def within(self, deadline: Deadline) -> ProcessManager:
        """Return a manager whose probe commands all finish by *deadline*.

        Each ``fuser``/``lsof``/``ps`` run is cut to what the deadline leaves,
        and one started after it raises ``PortProbeError`` without running.
        """
        bounded = ProcessManager()
        bounded._deadline = deadline
        return bounded

    @staticmethod
    def _run_dir() -> Path:
        return _user_run_dir()

    def read_port_file(self) -> int | None:
        """Read the daemon port from ``serve.port``.

        ``None`` is the documented answer when the file does not exist, and
        when its content does not parse: voxd publishes it by atomic rename,
        so an empty or partial file is a leftover from something else -- a
        stale hint, warned about and ignored. A read error other than
        not-found raises ``PortProbeError``.
        """
        port_file = self._run_dir() / "serve.port"
        try:
            text = port_file.read_text()
        except FileNotFoundError:
            return None
        except OSError as exc:
            msg = f"cannot read {port_file}: {exc}"
            raise PortProbeError(msg) from exc
        try:
            return int(text.strip())
        except ValueError:
            logger.warning("Ignoring stale %s: unparseable %r", port_file, text)
            return None

    def discard_port_file(self, port: int) -> PortFileCleanup:
        """Remove ``serve.port`` when it names *port* or does not parse.

        Runs under the lock voxd publishes the file with (see
        :class:`StalePortFile`). Returns what it did; a lock, read or removal
        failure raises ``PortProbeError`` so the caller can report it.
        """
        return StalePortFile.read(self._run_dir() / "serve.port").discard(port)

    def find_pid_on_port(self, port: int) -> list[int]:
        """Return all PIDs with connections to *port*.

        On macOS ``lsof`` lists only the TCP listener on *port* (a client
        whose peer port is *port* would otherwise match). On Linux ``fuser
        <port>/tcp`` returns space-separated PIDs, the daemon and connected
        clients alike. Returns **all** of them so the caller can check each
        for voxd identity.

        Fails closed: a missing tool, a timeout, or an error exit raises
        ``PortProbeError``. Only a silent "found nothing" (exit 1, no stdout
        and no stderr) reads as an empty port.
        """
        if platform.system() == "Darwin":
            # -w: lsof's warnings (an unreadable mount) are not errors and
            # must not fail the probe through a non-empty stderr.
            cmd = ["lsof", "-w", "-nP", "-t", f"-iTCP:{port}", "-sTCP:LISTEN"]
        else:
            cmd = ["fuser", f"{port}/tcp"]
        result = self._probe(cmd)
        out = result.stdout.strip()
        if self._found_nothing(result):
            return []
        if result.returncode != 0:
            msg = (
                f"cannot inspect port {port}: {cmd[0]} exited "
                f"{result.returncode}: {result.stderr.strip()}"
            )
            raise PortProbeError(msg)
        # isdecimal (not isdigit) is the strict subset int() always accepts:
        # it rejects superscripts like "²" that isdigit would pass.
        return [int(token) for token in re.split(r"[\s:]+", out) if token.isdecimal()]

    def identity(self, pid: int) -> ProcessIdentity | None:
        """Return *pid*'s start time and command line; ``None`` once it is gone.

        ``ps`` exits 1 and says nothing for a pid that does not exist; any
        other failure, or a line that does not parse, raises ``PortProbeError``
        rather than reading as "gone".
        """
        result = self._probe(["ps", "-p", str(pid), "-o", "lstart=", "-o", "command="])
        if self._found_nothing(result):
            return None
        if result.returncode != 0:
            msg = (
                f"cannot inspect pid {pid}: ps exited {result.returncode}: "
                f"{result.stderr.strip()}"
            )
            raise PortProbeError(msg)
        try:
            return ProcessIdentity.from_ps(result.stdout.strip())
        except ValueError as exc:
            raise PortProbeError(f"cannot inspect pid {pid}: {exc}") from exc

    def is_vox_daemon_process(self, pid: int) -> bool:
        """Return True when *pid* is alive and its command line runs voxd."""
        seen = self.identity(pid)
        if seen is not None and seen.is_voxd():
            return True
        logger.warning(
            "PID %d is not a voxd process (command: %s)",
            pid,
            seen.command if seen is not None else "<gone>",
        )
        return False

    def open_voxd(self, pid: int) -> ProcessHandle | None:
        """Return a handle pinned to *pid*; ``None`` when it is gone or not voxd."""
        return DaemonProcess.on_this_host(self.identity).open(pid)

    def kill_pid(self, pid: int) -> bool:
        """Terminate the voxd process *pid* through a pinned handle.

        Return True when no voxd process remains under that handle (killed, or
        already gone), False when it survived (e.g. permission denied).
        """
        handle = self.open_voxd(pid)
        if handle is None:
            logger.info("PID %d is gone or is not voxd; nothing to kill", pid)
            return True
        logger.info("Terminating voxd PID %d", pid)
        return handle.terminate(self._KILL_BUDGET_S) is Termination.EXITED

    @staticmethod
    def _found_nothing(result: subprocess.CompletedProcess[str]) -> bool:
        """Return True for the tools' silent "no match": exit 1, no output.

        An emptiness check, not a text match: any stderr at all means the
        tool failed, whatever its locale said.
        """
        return (
            result.returncode == 1
            and not result.stdout.strip()
            and not result.stderr.strip()
        )

    def _probe(self, cmd: list[str]) -> subprocess.CompletedProcess[str]:
        """Run one probe command; raise ``PortProbeError`` if it cannot run.

        The C locale fixes ``ps -o lstart`` at its 24-character form. The run
        is cut to what this manager's deadline leaves.
        """
        timeout = self._deadline.cap(
            _SUBPROCESS_TIMEOUT_SECONDS, f"cannot run {cmd[0]}"
        )
        try:
            return subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                env={**os.environ, "LC_ALL": "C"},
            )
        except FileNotFoundError as exc:
            hint = " (install psmisc)" if cmd[0] == "fuser" else ""
            msg = f"cannot run {cmd[0]}: not found{hint}"
            raise PortProbeError(msg) from exc
        except (OSError, subprocess.TimeoutExpired) as exc:
            msg = f"cannot run {cmd[0]}: {exc}"
            raise PortProbeError(msg) from exc

    def kill_stale_daemon(self) -> bool:
        """Kill a daemon process occupying the port.  Return True if killed.

        ``serve.port`` is read once, before the port is probed: the port acted
        on and the file pinned for removal come from that one open file. It
        is cleared after the kill only if it is still that file, so a voxd
        that binds and publishes at any point after the read -- the service
        manager respawning it while the old one dies -- keeps its own.
        """
        self._note = ""
        stale = StalePortFile.read(self._run_dir() / "serve.port")
        port = stale.port if stale.port is not None else DEFAULT_PORT
        pid = self._stale_voxd(port)
        if pid is None:
            return False
        if not self.kill_pid(pid):
            logger.warning("Failed to kill PID %d — leaving state files intact", pid)
            return False
        self._clear_after_kill(stale, port)
        return True

    def _stale_voxd(self, port: int) -> int | None:
        """Return the first voxd pid on *port*; ``None`` when no holder is voxd."""
        pids = self.find_pid_on_port(port)
        for pid in pids:
            if self.is_vox_daemon_process(pid):
                logger.info("Found stale daemon PID %d on port %d", pid, port)
                return pid
            logger.debug(
                "PID %d on port %d is not a voxd process — skipping", pid, port
            )
        if pids:
            logger.warning("No voxd process found among PIDs %s on port %d", pids, port)
        return None

    def _clear_after_kill(self, stale: StalePortFile, port: int) -> None:
        """Clear ``serve.port`` once the daemon is confirmed gone.

        The kill already happened, so a file that cannot be cleared must not
        fail the caller -- install would abort with no daemon and no unit. It
        is logged and kept as :attr:`port_file_note` for the caller to report.
        """
        try:
            stale.discard(port)
        except PortProbeError as exc:
            logger.warning("voxd stopped, but serve.port was not cleared: %s", exc)
            self._note = f"daemon stopped; could not clear serve.port: {exc}"

    def ensure_port_free(self) -> None:
        """Kill stale daemon if present; abort if port remains occupied.

        The post-kill re-check must target the ACTUAL port the daemon was
        last known to bind, not ``DEFAULT_PORT``.  The port file read
        MUST happen before ``kill_stale_daemon`` runs, because a
        successful kill clears ``serve.port`` -- which drops the very
        value we need for the post-kill re-check.
        """
        target_port = self.read_port_file() or DEFAULT_PORT
        self.kill_stale_daemon()
        pids = self.find_pid_on_port(target_port)
        if pids:
            msg = (
                f"Port {target_port} is still in use (PIDs: {pids})."
                " Stop the process and retry."
            )
            raise SystemExit(msg)
