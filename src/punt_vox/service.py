"""Daemon lifecycle management for ``vox serve``.

Provides ``install`` and ``uninstall`` commands that register vox as a
system service (launchd on macOS, systemd on Linux) so the daemon starts
at login and restarts on crash.

The service runs ``vox serve --port 8421`` using the Python interpreter
that executed the install command, anchoring to the exact venv/installation.
"""

from __future__ import annotations

import html
import logging
import os
import platform
import re
import shlex
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

from punt_vox.daemon import (
    DEFAULT_PORT,
    _remove_port_file,  # pyright: ignore[reportPrivateUsage]
    read_port_file,
)
from punt_vox.keys import write_keys_env

logger = logging.getLogger(__name__)

_LABEL = "com.punt-labs.vox"

_KILL_TIMEOUT_SECONDS = 5
_SUBPROCESS_TIMEOUT_SECONDS = 5


def _find_pid_on_port(port: int) -> list[int]:
    """Return all PIDs with connections to *port*.

    On macOS ``lsof -ti :<port>`` returns one PID per line (daemon *and*
    connected clients like mcp-proxy).  On Linux ``fuser <port>/tcp``
    returns space-separated PIDs.  We return **all** of them so the caller
    can check each for vox identity.
    """
    if platform.system() == "Darwin":
        cmd = ["lsof", "-ti", f":{port}"]
    else:
        cmd = ["fuser", f"{port}/tcp"]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=_SUBPROCESS_TIMEOUT_SECONDS
        )
        if result.returncode == 0 and result.stdout.strip():
            pids: list[int] = []
            # lsof: one PID per line.  fuser: "8421/tcp:  6789 1234".
            # Split the entire output on whitespace/colons and collect
            # every purely numeric token.
            for token in re.split(r"[\s:]+", result.stdout.strip()):
                if token.isdigit():
                    pids.append(int(token))
            return pids
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return []


def _is_vox_daemon_process(pid: int) -> bool:
    """Check whether *pid* is a vox daemon process.

    Uses ``ps`` to inspect the command line.  Returns False if the
    process doesn't look like ``punt_vox … serve``.
    """
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT_SECONDS,
        )
        cmd_line = result.stdout.strip()
        if re.search(r"\bserve\b", cmd_line) and (
            "punt_vox" in cmd_line
            or "punt-vox" in cmd_line
            or re.search(r"\bvox\b.*\bserve\b", cmd_line)
        ):
            return True
        logger.warning(
            "PID %d is not a vox daemon (command: %s)", pid, cmd_line or "<empty>"
        )
    except (OSError, subprocess.TimeoutExpired):
        logger.warning("Could not verify PID %d identity via ps", pid)
    return False


def _kill_pid(pid: int) -> bool:
    """Send SIGTERM then SIGKILL after timeout.

    Returns True if the process is confirmed gone (killed or was already
    dead).  Returns False if the kill could not be performed (e.g.
    PermissionError).
    """
    logger.info("Sending SIGTERM to PID %d", pid)
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        logger.info("PID %d already gone", pid)
        return True
    except PermissionError:
        logger.warning("No permission to signal PID %d — owned by another user?", pid)
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


def _kill_stale_daemon() -> bool:
    """Kill a daemon process occupying the port.  Returns True if killed."""
    port = read_port_file()
    if port is None:
        port = DEFAULT_PORT
    pids = _find_pid_on_port(port)
    if not pids:
        return False
    for pid in pids:
        if not _is_vox_daemon_process(pid):
            logger.debug(
                "PID %d on port %d is not a vox daemon — skipping",
                pid,
                port,
            )
            continue
        logger.info("Found stale daemon PID %d on port %d", pid, port)
        if _kill_pid(pid):
            _remove_port_file()
            return True
        logger.warning("Failed to kill PID %d — leaving state files intact", pid)
        return False
    logger.warning("No vox daemon found among PIDs %s on port %d", pids, port)
    return False


def _ensure_port_free() -> None:
    """Kill stale daemon if present; abort if port remains occupied."""
    _kill_stale_daemon()
    pids = _find_pid_on_port(DEFAULT_PORT)
    if pids:
        msg = (
            f"Port {DEFAULT_PORT} is still in use (PIDs: {pids})."
            " Stop the process and retry."
        )
        raise SystemExit(msg)


def _vox_exec_args() -> list[str]:
    """Return the command to invoke ``vox serve`` from the current Python."""
    return [sys.executable, "-m", "punt_vox", "serve", "--port", str(DEFAULT_PORT)]


# ---------------------------------------------------------------------------
# macOS — launchd
# ---------------------------------------------------------------------------

_LAUNCHD_DIR = Path.home() / "Library" / "LaunchAgents"
_LAUNCHD_PLIST = _LAUNCHD_DIR / f"{_LABEL}.plist"


def _launchd_plist_content() -> str:
    args = _vox_exec_args()
    # Plist XML reads <string> values literally — use html.escape for
    # XML-safe encoding (not shlex.quote, which adds shell quotes).
    program_args = "\n".join(f"        <string>{html.escape(a)}</string>" for a in args)
    log_dir = html.escape(str(Path.home() / ".punt-vox" / "logs"))
    path_value = html.escape(os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin"))
    return textwrap.dedent(f"""\
        <?xml version="1.0" encoding="UTF-8"?>
        <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
          "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
        <plist version="1.0">
        <dict>
            <key>Label</key>
            <string>{_LABEL}</string>
            <key>ProgramArguments</key>
            <array>
        {program_args}
            </array>
            <key>EnvironmentVariables</key>
            <dict>
                <key>PATH</key>
                <string>{path_value}</string>
            </dict>
            <key>RunAtLoad</key>
            <true/>
            <key>KeepAlive</key>
            <true/>
            <key>StandardOutPath</key>
            <string>{log_dir}/daemon-stdout.log</string>
            <key>StandardErrorPath</key>
            <string>{log_dir}/daemon-stderr.log</string>
        </dict>
        </plist>
    """)


def _launchd_install() -> None:
    _ensure_port_free()
    _LAUNCHD_DIR.mkdir(parents=True, exist_ok=True)
    # Ensure log directory exists — launchd won't create it.
    log_dir = Path.home() / ".punt-vox" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    # Unload first if already loaded — launchctl load fails with I/O error
    # if the plist is already loaded (happens on every upgrade).
    if _LAUNCHD_PLIST.exists():
        subprocess.run(
            ["launchctl", "unload", "-w", str(_LAUNCHD_PLIST)],
            check=False,  # may not be loaded
        )
        logger.info("Unloaded existing %s", _LABEL)

    _LAUNCHD_PLIST.write_text(_launchd_plist_content())
    logger.info("Wrote %s", _LAUNCHD_PLIST)

    subprocess.run(
        ["launchctl", "load", "-w", str(_LAUNCHD_PLIST)],
        check=True,
    )
    logger.info("Loaded %s into launchd", _LABEL)


def _launchd_uninstall() -> None:
    if _LAUNCHD_PLIST.exists():
        subprocess.run(
            ["launchctl", "unload", "-w", str(_LAUNCHD_PLIST)],
            check=False,  # may already be unloaded
        )
        _LAUNCHD_PLIST.unlink()
        logger.info("Removed %s", _LAUNCHD_PLIST)
    else:
        logger.info("No plist found at %s — nothing to uninstall", _LAUNCHD_PLIST)
    _kill_stale_daemon()


def _launchd_status() -> bool:
    result = subprocess.run(
        ["launchctl", "list", _LABEL],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


# ---------------------------------------------------------------------------
# Linux — systemd user unit
# ---------------------------------------------------------------------------

_SYSTEMD_DIR = Path.home() / ".config" / "systemd" / "user"
_SYSTEMD_UNIT = _SYSTEMD_DIR / "vox.service"


def _systemd_unit_content() -> str:
    args = _vox_exec_args()
    exec_start = " ".join(shlex.quote(a) for a in args)
    raw_path = os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")
    path_value = raw_path.replace("%", "%%")
    return textwrap.dedent(f"""\
        [Unit]
        Description=Vox text-to-speech daemon
        After=network.target

        [Service]
        ExecStart={exec_start}
        Environment="PATH={path_value}"
        Restart=on-failure
        RestartSec=5

        [Install]
        WantedBy=default.target
    """)


def _systemd_install() -> None:
    _ensure_port_free()
    _SYSTEMD_DIR.mkdir(parents=True, exist_ok=True)

    # Stop if already running — systemd won't pick up new unit config
    # from enable --now alone.
    if _SYSTEMD_UNIT.exists():
        subprocess.run(
            ["systemctl", "--user", "stop", "vox"],
            check=False,  # may not be running
        )
        logger.info("Stopped existing vox.service")

    _SYSTEMD_UNIT.write_text(_systemd_unit_content())
    logger.info("Wrote %s", _SYSTEMD_UNIT)

    subprocess.run(
        ["systemctl", "--user", "daemon-reload"],
        check=True,
    )
    subprocess.run(
        ["systemctl", "--user", "enable", "--now", "vox"],
        check=True,
    )
    logger.info("Enabled and started vox.service")


def _systemd_uninstall() -> None:
    if _SYSTEMD_UNIT.exists():
        subprocess.run(
            ["systemctl", "--user", "disable", "--now", "vox"],
            check=False,  # may already be stopped
        )
        _SYSTEMD_UNIT.unlink()
        subprocess.run(
            ["systemctl", "--user", "daemon-reload"],
            check=False,
        )
        logger.info("Removed %s", _SYSTEMD_UNIT)
    else:
        logger.info("No unit found at %s — nothing to uninstall", _SYSTEMD_UNIT)
    _kill_stale_daemon()


def _systemd_status() -> bool:
    result = subprocess.run(
        ["systemctl", "--user", "is-active", "vox"],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() == "active"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect_platform() -> str:
    """Return ``'macos'`` or ``'linux'``.  Raises on unsupported platforms."""
    system = platform.system()
    if system == "Darwin":
        return "macos"
    if system == "Linux":
        return "linux"
    msg = (
        f"Unsupported platform: {system}. vox daemon install supports macOS and Linux."
    )
    raise SystemExit(msg)


def install() -> str:
    """Install vox as a system service.  Returns a status message."""
    plat = detect_platform()
    args = _vox_exec_args()

    keys_path = write_keys_env(dict(os.environ))
    logger.info("Wrote provider keys to %s", keys_path)

    if plat == "macos":
        _launchd_install()
        running = _launchd_status()
    else:
        _systemd_install()
        running = _systemd_status()

    exec_display = " ".join(args)
    status = "running" if running else "installed (not yet running)"
    lines = [
        f"vox daemon {status} on port {DEFAULT_PORT}.",
        f"  Service: {_LAUNCHD_PLIST if plat == 'macos' else _SYSTEMD_UNIT}",
        f"  Keys:    {keys_path}",
        f"  Command: {exec_display}",
    ]
    if plat == "linux" and not _has_linger():
        lines.append(
            "  Warning: loginctl linger is not enabled. "
            "The daemon will stop when you log out. "
            "Run: loginctl enable-linger"
        )
    return os.linesep.join(lines)


def uninstall() -> str:
    """Remove vox system service.  Returns a status message."""
    plat = detect_platform()
    if plat == "macos":
        _launchd_uninstall()
        path = _LAUNCHD_PLIST
    else:
        _systemd_uninstall()
        path = _SYSTEMD_UNIT
    return f"vox daemon uninstalled. Removed {path}."


def is_running() -> bool:
    """Check if the daemon service is currently running."""
    plat = detect_platform()
    if plat == "macos":
        return _launchd_status()
    return _systemd_status()


def _has_linger() -> bool:
    """Check if loginctl linger is enabled for the current user (Linux only)."""
    import pwd

    try:
        username = pwd.getpwuid(os.getuid()).pw_name
        result = subprocess.run(
            ["loginctl", "show-user", username, "--property=Linger"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return "Linger=yes" in result.stdout
    except (OSError, KeyError, subprocess.TimeoutExpired):
        return False
