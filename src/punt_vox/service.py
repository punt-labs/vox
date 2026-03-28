"""Daemon lifecycle management for ``vox serve``.

Provides ``install`` and ``uninstall`` commands that register vox as a
system service (launchd on macOS, systemd on Linux) so the daemon starts
at login and restarts on crash.

The service runs ``vox serve --port 8421`` using the Python interpreter
that executed the install command, anchoring to the exact venv/installation.
"""

from __future__ import annotations

import contextlib
import html
import logging
import os
import platform
import shlex
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

from punt_vox.daemon import DEFAULT_PORT, read_port_file

logger = logging.getLogger(__name__)

_LABEL = "com.punt-labs.vox"

_STATE_DIR = Path.home() / ".punt-vox"
_PORT_FILE = _STATE_DIR / "serve.port"
_TOKEN_FILE = _STATE_DIR / "serve.token"

_KILL_TIMEOUT_SECONDS = 5


def _find_pid_on_port(port: int) -> int | None:
    """Find the PID listening on *port*.  Returns ``None`` if nothing found."""
    if platform.system() == "Darwin":
        cmd = ["lsof", "-ti", f":{port}"]
    else:
        cmd = ["fuser", f"{port}/tcp"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if result.returncode == 0 and result.stdout.strip():
            # lsof/fuser may return multiple PIDs — take the first.
            return int(result.stdout.strip().splitlines()[0].strip())
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return None


def _kill_pid(pid: int) -> None:
    """Send SIGTERM then SIGKILL after timeout."""
    logger.info("Sending SIGTERM to PID %d", pid)
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        logger.info("PID %d already gone", pid)
        return

    deadline = time.monotonic() + _KILL_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)  # probe — raises if gone
        except ProcessLookupError:
            logger.info("PID %d exited after SIGTERM", pid)
            return
        time.sleep(0.25)

    logger.warning(
        "PID %d did not exit after %ds — sending SIGKILL",
        pid,
        _KILL_TIMEOUT_SECONDS,
    )
    with contextlib.suppress(ProcessLookupError):
        os.kill(pid, signal.SIGKILL)


def _remove_state_files() -> None:
    """Remove port and token files."""
    for path in (_PORT_FILE, _TOKEN_FILE):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not remove %s", path)


def _kill_stale_daemon() -> bool:
    """Kill a daemon process occupying the port.  Returns True if killed."""
    port = read_port_file() or DEFAULT_PORT
    pid = _find_pid_on_port(port)
    if pid is None:
        return False
    logger.info("Found stale daemon PID %d on port %d", pid, port)
    _kill_pid(pid)
    _remove_state_files()
    return True


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
    _kill_stale_daemon()
    _LAUNCHD_DIR.mkdir(parents=True, exist_ok=True)
    # Ensure log directory exists — launchd won't create it.
    log_dir = Path.home() / ".punt-vox" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
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
    return textwrap.dedent(f"""\
        [Unit]
        Description=Vox text-to-speech daemon
        After=network.target

        [Service]
        ExecStart={exec_start}
        Restart=on-failure
        RestartSec=5

        [Install]
        WantedBy=default.target
    """)


def _systemd_install() -> None:
    _kill_stale_daemon()
    _SYSTEMD_DIR.mkdir(parents=True, exist_ok=True)
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
