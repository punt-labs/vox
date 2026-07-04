"""Process management for the voxd daemon."""

from __future__ import annotations

import logging
import os
import platform
import re
import signal
import subprocess
import time
from pathlib import Path
from typing import Self

from punt_vox.paths import run_dir as _user_run_dir

logger = logging.getLogger(__name__)

_KILL_TIMEOUT_SECONDS = 5
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
        """
        if platform.system() == "Darwin":
            cmd = ["lsof", "-ti", f":{port}"]
        else:
            cmd = ["fuser", f"{port}/tcp"]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=_SUBPROCESS_TIMEOUT_SECONDS
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            # Probe TOOL failed -- a broken probe must not read as an empty port.
            logger.warning(
                "Could not probe port %d for PIDs via %s: %s", port, cmd[0], exc
            )
            return []
        out = result.stdout.strip()
        if result.returncode != 0 or not out:
            return []  # tool ran cleanly, found nothing -- port genuinely empty
        # isdecimal (not isdigit) is the strict subset int() always accepts:
        # it rejects superscripts like "²" that isdigit would pass.
        return [int(token) for token in re.split(r"[\s:]+", out) if token.isdecimal()]

    def is_vox_daemon_process(self, pid: int) -> bool:
        """Check whether *pid* is a voxd daemon process.

        Uses ``ps`` to inspect the command line.  Returns False if the
        process doesn't look like ``voxd``.
        """
        try:
            result = subprocess.run(
                ["ps", "-p", str(pid), "-o", "command="],
                capture_output=True,
                text=True,
                timeout=_SUBPROCESS_TIMEOUT_SECONDS,
            )
            cmd_line = result.stdout.strip()
            if re.search(r"\bvoxd\b", cmd_line):
                return True
            # Also match the old "vox serve" pattern for upgrade transitions.
            if re.search(r"\bserve\b", cmd_line) and (
                "punt_vox" in cmd_line
                or "punt-vox" in cmd_line
                or re.search(r"\bvox\b.*\bserve\b", cmd_line)
            ):
                return True
            logger.warning(
                "PID %d is not a voxd process (command: %s)", pid, cmd_line or "<empty>"
            )
        except (OSError, subprocess.TimeoutExpired):
            logger.warning("Could not verify PID %d identity via ps", pid)
        return False

    def kill_pid(self, pid: int) -> bool:
        """Send SIGTERM then SIGKILL after timeout.

        Return True if the process is confirmed gone (killed or was
        already dead).  Return False if the kill could not be performed
        (e.g. PermissionError).
        """
        logger.info("Sending SIGTERM to PID %d", pid)
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            logger.info("PID %d already gone", pid)
            return True
        except PermissionError:
            logger.warning(
                "No permission to signal PID %d — owned by another user?",
                pid,
            )
            return False

        deadline = time.monotonic() + _KILL_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)  # probe — raises if gone
            except ProcessLookupError:
                logger.info("PID %d exited after SIGTERM", pid)
                return True
            time.sleep(0.25)

        logger.warning(
            "PID %d did not exit after %ds — sending SIGKILL",
            pid,
            _KILL_TIMEOUT_SECONDS,
        )
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            logger.info("PID %d already gone before SIGKILL", pid)
            return True
        except PermissionError:
            logger.warning("No permission to SIGKILL PID %d", pid)
            return False

        # Probe briefly to confirm SIGKILL took effect.
        kill_deadline = time.monotonic() + 2
        while time.monotonic() < kill_deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                logger.info("PID %d exited after SIGKILL", pid)
                return True
            time.sleep(0.25)
        logger.warning("PID %d still alive after SIGKILL", pid)
        return False

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
