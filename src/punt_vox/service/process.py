"""Process management for the voxd daemon."""

from __future__ import annotations

import logging
import os
import platform
import re
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Self

from punt_vox.paths import run_dir as _user_run_dir
from punt_vox.service.backend import PortProbeError
from punt_vox.service.daemon_process import DaemonProcess
from punt_vox.service.process_identity import ProcessIdentity

if TYPE_CHECKING:
    from punt_vox.service.backend import ProcessHandle

logger = logging.getLogger(__name__)

_SUBPROCESS_TIMEOUT_SECONDS = 5

DEFAULT_PORT = 8421


class ProcessManager:
    """Manage voxd daemon processes: find, verify, kill, and port-file I/O."""

    __slots__ = ()

    def __new__(cls) -> Self:
        return super().__new__(cls)

    @staticmethod
    def _run_dir() -> Path:
        return _user_run_dir()

    def read_port_file(self) -> int | None:
        """Read the daemon port from the port file.  Return None if missing."""
        port_file = self._run_dir() / "serve.port"
        try:
            return int(port_file.read_text().strip())
        except (FileNotFoundError, ValueError, OSError):
            return None

    def remove_port_file(self) -> None:
        """Remove the daemon port file."""
        port_file = self._run_dir() / "serve.port"
        try:
            port_file.unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not remove %s", port_file)
        logger.info("Removed port file")

    def find_pid_on_port(self, port: int) -> list[int]:
        """Return all PIDs with connections to *port*.

        On macOS ``lsof -ti :<port>`` returns one PID per line (daemon
        *and* connected clients).  On Linux ``fuser <port>/tcp`` returns
        space-separated PIDs.  Returns **all** of them so the caller can
        check each for voxd identity.

        Fails closed: a missing tool, a timeout, or an error exit raises
        ``PortProbeError``. Only a clean "found nothing" (exit 1, no output)
        reads as an empty port.
        """
        if platform.system() == "Darwin":
            cmd = ["lsof", "-ti", f":{port}"]
        else:
            cmd = ["fuser", f"{port}/tcp"]
        result = self._probe(cmd)
        out = result.stdout.strip()
        if result.returncode == 1 and not out:
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

        ``ps`` exits 1 with no output for a pid that does not exist; any other
        failure raises ``PortProbeError`` rather than reading as "gone".
        """
        result = self._probe(["ps", "-p", str(pid), "-o", "lstart=", "-o", "command="])
        out = result.stdout.strip()
        if result.returncode == 1 and not out:
            return None
        if result.returncode != 0:
            msg = f"cannot inspect pid {pid}: ps exited {result.returncode}"
            raise PortProbeError(msg)
        return ProcessIdentity.from_ps(out)

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
        return handle.terminate()

    @staticmethod
    def _probe(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        """Run one probe command; raise ``PortProbeError`` if it cannot run.

        The C locale fixes ``ps -o lstart`` at its 24-character form.
        """
        try:
            return subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=_SUBPROCESS_TIMEOUT_SECONDS,
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
        """Kill a daemon process occupying the port.  Return True if killed."""
        port = self.read_port_file()
        if port is None:
            port = DEFAULT_PORT
        pids = self.find_pid_on_port(port)
        if not pids:
            return False
        for pid in pids:
            if not self.is_vox_daemon_process(pid):
                logger.debug(
                    "PID %d on port %d is not a voxd process — skipping",
                    pid,
                    port,
                )
                continue
            logger.info("Found stale daemon PID %d on port %d", pid, port)
            if self.kill_pid(pid):
                self.remove_port_file()
                return True
            logger.warning("Failed to kill PID %d — leaving state files intact", pid)
            return False
        logger.warning("No voxd process found among PIDs %s on port %d", pids, port)
        return False

    def ensure_port_free(self) -> None:
        """Kill stale daemon if present; abort if port remains occupied.

        The post-kill re-check must target the ACTUAL port the daemon was
        last known to bind, not ``DEFAULT_PORT``.  The port file read
        MUST happen before ``kill_stale_daemon`` runs, because a
        successful kill calls ``remove_port_file`` -- which drops the
        very value we need for the post-kill re-check.
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
