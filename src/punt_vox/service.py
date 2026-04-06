"""Daemon lifecycle management for ``voxd``.

Provides ``install`` and ``uninstall`` commands that register voxd as a
system service (launchd on macOS, systemd on Linux) so the daemon starts
at boot and restarts on crash.

The service runs ``voxd --port 8421`` as a system-level daemon:
- macOS: ``/Library/LaunchDaemons/com.punt-labs.voxd.plist``
- Linux: ``/etc/systemd/system/voxd.service``

Both require ``sudo`` for installation.
"""

from __future__ import annotations

import getpass
import html
import logging
import os
import platform
import pwd
import re
import shlex
import shutil
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_PORT = 8421

_LABEL = "com.punt-labs.voxd"

_KILL_TIMEOUT_SECONDS = 5
_SUBPROCESS_TIMEOUT_SECONDS = 5


# ---------------------------------------------------------------------------
# System paths — duplicated from voxd.py to avoid importing heavy providers.
# TODO: extract into a shared lightweight module.
# ---------------------------------------------------------------------------


def _data_root() -> Path:
    """Resolve system data root: Homebrew prefix on macOS, / on Linux."""
    if sys.platform == "darwin":
        try:
            prefix = subprocess.check_output(
                ["brew", "--prefix"], text=True, timeout=5
            ).strip()
            return Path(prefix)
        except (subprocess.SubprocessError, FileNotFoundError):
            return Path("/usr/local")  # fallback for non-Homebrew macOS
    return Path("/")  # type: ignore[unreachable,unused-ignore]


def _config_dir() -> Path:
    return _data_root() / "etc" / "vox"


def _log_dir() -> Path:
    return _data_root() / "var" / "log" / "vox"


def _run_dir() -> Path:
    return _data_root() / "var" / "run" / "vox"


def _cache_dir() -> Path:
    return _data_root() / "var" / "cache" / "vox"


# ---------------------------------------------------------------------------
# User detection
# ---------------------------------------------------------------------------


def _installing_user() -> str:
    """Get the real user, not root, when running under sudo."""
    return os.environ.get("SUDO_USER") or getpass.getuser()


# ---------------------------------------------------------------------------
# Keys.env writing (inline — system config dir, not ~/.punt-labs/vox/)
# ---------------------------------------------------------------------------

_PROVIDER_KEY_NAMES: frozenset[str] = frozenset(
    {
        "ELEVENLABS_API_KEY",
        "OPENAI_API_KEY",
        "AWS_PROFILE",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_DEFAULT_REGION",
        "TTS_PROVIDER",
        "TTS_MODEL",
    }
)


def _write_keys_env(env: dict[str, str], config_dir: Path) -> Path:
    """Write keys.env to the system config dir.  chmod 0600."""
    path = config_dir / "keys.env"

    existing: dict[str, str] = {}
    if path.exists():
        try:
            text = path.read_text()
            for line in text.splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                if "=" not in stripped:
                    continue
                key, _, value = stripped.partition("=")
                key = key.strip()
                value = value.strip()
                if key:
                    existing[key] = value
        except OSError as exc:
            logger.warning("Could not read existing %s: %s — will overwrite", path, exc)

    merged = dict(existing)
    for k in _PROVIDER_KEY_NAMES:
        if k in env:
            if env[k]:
                merged[k] = env[k]
            else:
                merged.pop(k, None)

    header = (
        "# vox provider keys — loaded by voxd at startup\n"
        "# Written by: vox daemon install\n\n"
    )
    lines = [f"{k}={v}" for k, v in sorted(merged.items()) if v]
    content = header + "\n".join(lines) + "\n"

    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, content.encode())
    finally:
        os.close(fd)
    path.chmod(0o600)
    return path


# ---------------------------------------------------------------------------
# Port file helpers
# ---------------------------------------------------------------------------


def read_port_file() -> int | None:
    """Read the daemon port from the port file. Returns None if missing."""
    port_file = _run_dir() / "serve.port"
    try:
        return int(port_file.read_text().strip())
    except (FileNotFoundError, ValueError, OSError):
        return None


def _remove_port_file() -> None:
    port_file = _run_dir() / "serve.port"
    try:
        port_file.unlink(missing_ok=True)
    except OSError:
        logger.warning("Could not remove %s", port_file)
    logger.info("Removed port file")


# ---------------------------------------------------------------------------
# Process management
# ---------------------------------------------------------------------------


def _find_pid_on_port(port: int) -> list[int]:
    """Return all PIDs with connections to *port*.

    On macOS ``lsof -ti :<port>`` returns one PID per line (daemon *and*
    connected clients).  On Linux ``fuser <port>/tcp`` returns
    space-separated PIDs.  We return **all** of them so the caller can
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
                "PID %d on port %d is not a voxd process — skipping",
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
    logger.warning("No voxd process found among PIDs %s on port %d", pids, port)
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


def _voxd_exec_args() -> list[str]:
    """Return the command to invoke ``voxd``."""
    voxd_path = shutil.which("voxd")
    if voxd_path is None:
        msg = (
            "voxd binary not found on PATH. "
            "Install with 'uv tool install punt-vox' "
            "or ensure ~/.local/bin is on your PATH."
        )
        raise SystemExit(msg)
    return [voxd_path, "--port", str(DEFAULT_PORT)]


# ---------------------------------------------------------------------------
# Directory creation
# ---------------------------------------------------------------------------


def _ensure_system_dirs(user: str) -> None:
    """Create system data directories with appropriate ownership."""
    dirs = [_config_dir(), _log_dir(), _run_dir(), _cache_dir()]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)
        logger.info("Ensured directory: %s", d)

    # On macOS/Linux, set ownership to the installing user (not root).
    if os.getuid() == 0:
        try:
            pw = pwd.getpwnam(user)
            uid, gid = pw.pw_uid, pw.pw_gid
            for d in dirs:
                os.chown(str(d), uid, gid)
                logger.info("Set ownership of %s to %s (%d:%d)", d, user, uid, gid)
        except KeyError:
            logger.warning("User %s not found — skipping chown", user)


# ---------------------------------------------------------------------------
# macOS — launchd (system-level)
# ---------------------------------------------------------------------------

_LAUNCHD_DIR = Path("/Library/LaunchDaemons")
_LAUNCHD_PLIST = _LAUNCHD_DIR / f"{_LABEL}.plist"


def _launchd_plist_content(user: str) -> str:
    args = _voxd_exec_args()
    # Plist XML reads <string> values literally — use html.escape for
    # XML-safe encoding (not shlex.quote, which adds shell quotes).
    program_args = "\n".join(f"        <string>{html.escape(a)}</string>" for a in args)
    log_dir = _log_dir()
    stdout_log = html.escape(str(log_dir / "voxd-stdout.log"))
    stderr_log = html.escape(str(log_dir / "voxd-stderr.log"))
    path_value = html.escape(os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin"))
    escaped_user = html.escape(user)
    return textwrap.dedent(f"""\
        <?xml version="1.0" encoding="UTF-8"?>
        <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
          "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
        <plist version="1.0">
        <dict>
            <key>Label</key>
            <string>{_LABEL}</string>
            <key>UserName</key>
            <string>{escaped_user}</string>
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
            <string>{stdout_log}</string>
            <key>StandardErrorPath</key>
            <string>{stderr_log}</string>
        </dict>
        </plist>
    """)


def _launchd_install(user: str) -> None:
    # Unload first if already loaded — launchctl load fails with I/O error
    # if the plist is already loaded (happens on every upgrade).  Must happen
    # BEFORE _ensure_port_free() so launchd doesn't restart the killed process.
    if _LAUNCHD_PLIST.exists():
        result = subprocess.run(
            ["launchctl", "unload", "-w", str(_LAUNCHD_PLIST)],
            check=False,  # may not be loaded
        )
        if result.returncode == 0:
            logger.info("Unloaded existing %s", _LABEL)
        else:
            logger.debug(
                "launchctl unload exited %d (may not have been loaded)",
                result.returncode,
            )

    _ensure_port_free()

    _LAUNCHD_PLIST.write_text(_launchd_plist_content(user))
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
# Linux — systemd (system-level)
# ---------------------------------------------------------------------------

_SYSTEMD_DIR = Path("/etc/systemd/system")
_SYSTEMD_UNIT = _SYSTEMD_DIR / "voxd.service"


def _safe_systemd_value(value: str) -> bool:
    """Return True if *value* is safe to embed in a systemd Environment= line.

    Rejects newlines, double quotes, and backslashes. Systemd interprets
    C-style escapes in double-quoted strings, so a backslash could escape
    the closing quote or inject control characters.
    """
    return not any(c in value for c in '\n\r"\\')


def _systemd_audio_env_lines(user: str) -> list[str]:
    """Build Environment= lines for audio-related env vars.

    PulseAudio/PipeWire need XDG_RUNTIME_DIR to find the user socket.
    Some setups also require PULSE_SERVER or DBUS_SESSION_BUS_ADDRESS.
    Only includes variables that are actually set at install time.

    When XDG_RUNTIME_DIR is absent (common under ``sudo``, which strips
    session env vars), compute the standard path from the target user's
    UID: ``/run/user/<uid>``.
    """
    audio_vars = ("XDG_RUNTIME_DIR", "PULSE_SERVER", "DBUS_SESSION_BUS_ADDRESS")
    lines: list[str] = []
    for name in audio_vars:
        value = os.environ.get(name)
        if value:
            if not _safe_systemd_value(value):
                logger.warning("Skipping %s: value contains unsafe characters", name)
                continue
            # Percent signs are special in systemd unit files.
            lines.append(f'Environment="{name}={value.replace("%", "%%")}"')
    # Deterministic fallback: sudo strips XDG_RUNTIME_DIR, but the
    # standard path is always /run/user/<uid> on systemd machines.
    if not any(line.startswith('Environment="XDG_RUNTIME_DIR=') for line in lines):
        try:
            uid = pwd.getpwnam(user).pw_uid
            lines.insert(0, f'Environment="XDG_RUNTIME_DIR=/run/user/{uid}"')
        except KeyError:
            logger.warning(
                "User %s not found — cannot compute XDG_RUNTIME_DIR fallback",
                user,
            )
    return lines


def _systemd_unit_content(user: str) -> str:
    args = _voxd_exec_args()
    exec_start = " ".join(shlex.quote(a) for a in args)
    raw_path = os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")
    path_value = raw_path.replace("%", "%%")
    audio_lines = _systemd_audio_env_lines(user)
    env_block = ("\n" + " " * 8).join(
        [f'Environment="PATH={path_value}"', *audio_lines]
    )
    return textwrap.dedent(f"""\
        [Unit]
        Description=Voxd text-to-speech daemon
        After=network.target

        [Service]
        User={user}
        ExecStart={exec_start}
        {env_block}
        RuntimeDirectory=vox
        RuntimeDirectoryMode=0700
        Restart=on-failure
        RestartSec=5

        [Install]
        WantedBy=multi-user.target
    """)


def _systemd_install(user: str) -> None:
    # Stop if already running — systemd won't pick up new unit config
    # from enable --now alone.  Must happen BEFORE _ensure_port_free()
    # so systemd doesn't restart the killed process (Restart=on-failure).
    if _SYSTEMD_UNIT.exists():
        result = subprocess.run(
            ["systemctl", "stop", "voxd"],
            check=False,  # may not be running
        )
        if result.returncode == 0:
            logger.info("Stopped existing voxd.service")
        else:
            logger.debug(
                "systemctl stop exited %d (may not have been running)",
                result.returncode,
            )

    _ensure_port_free()

    _SYSTEMD_UNIT.write_text(_systemd_unit_content(user))
    logger.info("Wrote %s", _SYSTEMD_UNIT)

    subprocess.run(
        ["systemctl", "daemon-reload"],
        check=True,
    )
    subprocess.run(
        ["systemctl", "enable", "--now", "voxd"],
        check=True,
    )
    logger.info("Enabled and started voxd.service")


def _systemd_uninstall() -> None:
    if _SYSTEMD_UNIT.exists():
        subprocess.run(
            ["systemctl", "disable", "--now", "voxd"],
            check=False,  # may already be stopped
        )
        _SYSTEMD_UNIT.unlink()
        subprocess.run(
            ["systemctl", "daemon-reload"],
            check=False,
        )
        logger.info("Removed %s", _SYSTEMD_UNIT)
    else:
        logger.info("No unit found at %s — nothing to uninstall", _SYSTEMD_UNIT)
    _kill_stale_daemon()


def _systemd_status() -> bool:
    result = subprocess.run(
        ["systemctl", "is-active", "voxd"],
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
    """Install voxd as a system service.  Returns a status message."""
    plat = detect_platform()
    user = _installing_user()
    args = _voxd_exec_args()

    # Create system directories with correct ownership.
    config_dir = _config_dir()
    _ensure_system_dirs(user)

    # Write provider keys to system config dir.
    keys_path = _write_keys_env(dict(os.environ), config_dir)
    logger.info("Wrote provider keys to %s", keys_path)

    if plat == "macos":
        _launchd_install(user)
        running = _launchd_status()
    else:
        _systemd_install(user)
        running = _systemd_status()

    exec_display = " ".join(args)
    status = "running" if running else "installed (not yet running)"
    lines = [
        f"voxd daemon {status} on port {DEFAULT_PORT}.",
        f"  Service: {_LAUNCHD_PLIST if plat == 'macos' else _SYSTEMD_UNIT}",
        f"  Keys:    {keys_path}",
        f"  Command: {exec_display}",
        f"  User:    {user}",
    ]
    return os.linesep.join(lines)


def uninstall() -> str:
    """Remove voxd system service.  Returns a status message."""
    plat = detect_platform()
    if plat == "macos":
        _launchd_uninstall()
        path = _LAUNCHD_PLIST
    else:
        _systemd_uninstall()
        path = _SYSTEMD_UNIT
    return f"voxd daemon uninstalled. Removed {path}."


def is_running() -> bool:
    """Check if the daemon service is currently running."""
    plat = detect_platform()
    if plat == "macos":
        return _launchd_status()
    return _systemd_status()
