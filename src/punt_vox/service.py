"""Daemon lifecycle management for ``voxd``.

Provides ``install`` and ``uninstall`` commands that register voxd as a
system service (launchd on macOS, systemd on Linux) so the daemon starts
at boot and restarts on crash.

The service runs ``voxd --port 8421`` as the installing user:
- macOS: ``/Library/LaunchDaemons/com.punt-labs.voxd.plist``
- Linux: ``/etc/systemd/system/voxd.service``

Privilege scope: ``vox daemon install`` runs as the installing user.
Per-user state under ``~/.punt-labs/vox/`` — keys, logs, runtime state,
cache — is created with normal user permissions. The only privileged
operations are four ``sudo`` subprocess calls per platform that place
the system service file and reload/enable/restart the daemon manager.
This keeps every file write to a user-controlled directory
unprivileged and eliminates the class of attacks that arose when the
entire install ran as root inside ``$HOME``. See DES-029 in
``DESIGN.md`` for the full rationale.
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
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

from punt_vox.paths import (
    ensure_user_dirs as _paths_ensure_user_dirs,
    keys_env_file as _paths_keys_env_file,
    run_dir as _user_run_dir,
    user_state_dir as _paths_user_state_dir,
)

logger = logging.getLogger(__name__)

DEFAULT_PORT = 8421

_LABEL = "com.punt-labs.voxd"

_KILL_TIMEOUT_SECONDS = 5
_SUBPROCESS_TIMEOUT_SECONDS = 5

_SUDO_NOTICE = (
    "Installing voxd as a system service. You may be prompted for your sudo password."
)


# ---------------------------------------------------------------------------
# Path helpers — thin wrappers over punt_vox.paths so tests can patch them
# inside this module. All paths resolve from the current process's home
# dir because install now runs as the invoking user — no sudo escalation,
# no ``SUDO_USER`` fallback, no cross-user resolution.
# ---------------------------------------------------------------------------


def _run_dir() -> Path:
    return _user_run_dir()


# ---------------------------------------------------------------------------
# Keys.env writing — writes directly to the installing user's state dir.
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


def _write_keys_env(env: dict[str, str], keys_path: Path) -> Path:
    """Write ``keys.env`` to ``keys_path``. chmod 0600.

    Preserves any keys already present in the file that the caller did
    not override. An empty string in ``env`` removes the key.

    Runs as the installing user in a user-owned directory. The
    kernel's normal permission checks are sufficient — no fd-based
    ownership dance or path-hardening is needed when the process
    cannot write outside its own home.

    Values containing ``\\n``, ``\\r``, or ``\\x00`` are rejected (not
    a privilege defense, just input sanitization — without this an
    attacker-controlled env var could smuggle extra key=value lines
    into the file).

    If an existing ``keys.env`` is unreadable (permission error,
    corruption, not-a-regular-file, non-UTF-8 bytes), the merge is
    skipped, the broken file is unlinked, and a fresh file is written
    from *env* alone. Unlinking the broken file is necessary because
    a chmod 000 or otherwise permission-locked inode blocks a naive
    truncating write too — the only reliable recovery is to remove it
    and create a new inode. Overwrite is the right policy: the old
    file was unreadable, the new file will have correct ownership and
    permissions, and the current shell's env vars are the source of
    truth at install time. Copilot 3048295101 on PR #162.
    """
    existing: dict[str, str] = {}
    force_fresh = False

    keys_path.parent.mkdir(parents=True, exist_ok=True)

    if keys_path.exists():
        try:
            existing_text = keys_path.read_text()
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning(
                "Could not read existing %s: %s — will overwrite with env values",
                keys_path,
                exc,
            )
            existing_text = ""
            force_fresh = True
        for line in existing_text.splitlines():
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

    merged = dict(existing)
    for k in _PROVIDER_KEY_NAMES:
        if k in env:
            if env[k]:
                value = env[k]
                if any(c in value for c in "\x00\n\r"):
                    logger.warning(
                        "Refusing to write %s: value contains control characters",
                        k,
                    )
                    continue
                merged[k] = value
            else:
                merged.pop(k, None)

    header = (
        "# vox provider keys — loaded by voxd at startup\n"
        "# Written by: vox daemon install\n"
        "# Edit with your normal editor — no sudo required.\n\n"
    )
    lines = [f"{k}={v}" for k, v in sorted(merged.items()) if v]
    content = header + "\n".join(lines) + "\n"

    if force_fresh:
        # The existing inode was unreadable — chmod 000 or similar
        # blocks a truncating write too. Unlink so we can create a
        # fresh inode with the correct mode. ``missing_ok=True``
        # because a concurrent unlink would be fine too.
        try:
            keys_path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning(
                "Could not unlink unreadable %s: %s — write may fail",
                keys_path,
                exc,
            )

    keys_path.write_text(content)
    keys_path.chmod(0o600)
    return keys_path


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
    """Return the command to invoke ``voxd``.

    Resolves ``voxd`` relative to ``sys.executable`` so the systemd unit
    always runs the binary from the same distribution that provided
    ``vox``. Using ``shutil.which`` would pick up whichever ``voxd`` is
    first on ``PATH`` — a stale binary from an earlier
    ``uv tool install`` could get baked into ``ExecStart=`` and override
    the current one. Anchoring to ``sys.executable`` eliminates that
    class of bug.
    """
    voxd_path = Path(sys.executable).parent / "voxd"
    if not voxd_path.exists():
        msg = (
            f"voxd binary not found at {voxd_path}. "
            "Reinstall punt-vox (uv tool install punt-vox or pip install punt-vox)."
        )
        raise SystemExit(msg)
    return [str(voxd_path), "--port", str(DEFAULT_PORT)]


# ---------------------------------------------------------------------------
# Directory creation
# ---------------------------------------------------------------------------


def _ensure_user_dirs() -> Path:
    """Create per-user state directories under ``~/.punt-labs/vox``.

    Returns the resolved state dir. Runs as the installing user, so
    ``punt_vox.paths.ensure_user_dirs`` is sufficient — no chown, no
    walk of parent components, no privileged fallback.
    """
    state_root = _paths_user_state_dir()
    _paths_ensure_user_dirs(state_root)
    logger.info("Ensured directory tree under %s", state_root)
    return state_root


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
    log_dir = _paths_user_state_dir() / "logs"
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
    """Install the launchd plist. Sudo is invoked four times.

    1. ``sudo install`` the plist into ``/Library/LaunchDaemons``.
    2. ``sudo launchctl unload -w`` any previously-loaded version
       (``check=False`` — idempotent, no-op on fresh install).
    3. ``sudo launchctl load -w`` the freshly installed plist.
    4. ``sudo launchctl kickstart -k system/<label>`` to force a
       restart even if launchd considers the service already running
       with the old ExecStart baked in.

    The plist content is written to a user-owned tmp file first and
    then placed into the system directory via ``install(1)``, so the
    only privileged file write is the single ``install`` invocation.

    Why kickstart? ``launchctl load`` on an already-loaded plist is a
    no-op — it does not restart the daemon to pick up the new
    ``ExecStart``. On upgrade from an older install, the running voxd
    would keep its stale binary/args until the user rebooted. The
    ``kickstart -k`` primitive (``-k`` means "kill and restart if
    running; start if not") is the only supported way to force that
    reload. Cursor Bugbot 3048294138 / Copilot 3048295072 on PR #162.
    """
    state_root = _paths_user_state_dir()
    tmp_plist = state_root / "com.punt-labs.voxd.plist.tmp"
    tmp_plist.write_text(_launchd_plist_content(user))
    logger.info("Wrote plist to %s", tmp_plist)

    print(_SUDO_NOTICE, file=sys.stderr)
    try:
        subprocess.run(
            [
                "sudo",
                "install",
                "-m",
                "644",
                "-o",
                "root",
                "-g",
                "wheel",
                str(tmp_plist),
                str(_LAUNCHD_PLIST),
            ],
            check=True,
        )
        logger.info("Installed %s", _LAUNCHD_PLIST)

        # Unload any previously-loaded version. ``check=False`` because
        # launchctl exits non-zero when the label is not registered
        # (fresh install); that path is expected, not an error.
        subprocess.run(
            ["sudo", "launchctl", "unload", "-w", str(_LAUNCHD_PLIST)],
            check=False,
        )

        subprocess.run(
            ["sudo", "launchctl", "load", "-w", str(_LAUNCHD_PLIST)],
            check=True,
        )
        logger.info("Loaded %s into launchd", _LABEL)

        # Force a restart so the running voxd picks up the new
        # ExecStart from the freshly installed plist, rather than
        # continuing to run with the stale args baked in at the
        # previous launchctl load.
        subprocess.run(
            ["sudo", "launchctl", "kickstart", "-k", f"system/{_LABEL}"],
            check=True,
        )
        logger.info("Kickstarted %s", _LABEL)
    finally:
        try:
            tmp_plist.unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not remove tmp plist %s", tmp_plist)


def _launchd_uninstall() -> None:
    if _LAUNCHD_PLIST.exists():
        print(_SUDO_NOTICE, file=sys.stderr)
        subprocess.run(
            ["sudo", "launchctl", "unload", "-w", str(_LAUNCHD_PLIST)],
            check=False,  # may already be unloaded
        )
        subprocess.run(
            ["sudo", "rm", "-f", str(_LAUNCHD_PLIST)],
            check=True,
        )
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

    When ``XDG_RUNTIME_DIR`` is absent, compute the standard path from
    *user*'s UID: ``/run/user/<uid>``.
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
    # Deterministic fallback for XDG_RUNTIME_DIR: the standard path is
    # always /run/user/<uid> on systemd machines.
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
    # Runtime state lives in the user's home dir (see punt_vox.paths),
    # so there is no RuntimeDirectory= here — systemd does not need to
    # create /run/vox.
    return textwrap.dedent(f"""\
        [Unit]
        Description=Voxd text-to-speech daemon
        After=network.target

        [Service]
        User={user}
        ExecStart={exec_start}
        {env_block}
        Restart=on-failure
        RestartSec=5

        [Install]
        WantedBy=multi-user.target
    """)


def _systemd_install(user: str) -> None:
    """Install the systemd unit. Sudo is invoked four times.

    1. ``sudo install`` the unit into ``/etc/systemd/system``.
    2. ``sudo systemctl daemon-reload`` so systemd picks up the new
       unit file.
    3. ``sudo systemctl enable voxd`` to persist the unit across
       reboots (idempotent — safe to run on every install).
    4. ``sudo systemctl restart voxd`` to unconditionally (re)start
       the service with the current unit content.

    The unit content is written to a user-owned tmp file first and
    then placed into the system directory via ``install(1)``, so the
    only privileged file write is the single ``install`` invocation.

    Why restart, not ``enable --now``? ``enable --now`` only starts
    the service if it is not already running — on upgrade from an
    older install, it would leave the previous voxd process alive
    with the stale ``ExecStart``. ``systemctl restart`` is the only
    primitive that unconditionally cycles the process through the
    freshly-loaded unit file. Cursor Bugbot 3048294138 / Copilot
    3048295072 on PR #162.
    """
    state_root = _paths_user_state_dir()
    tmp_unit = state_root / "voxd.service.tmp"
    tmp_unit.write_text(_systemd_unit_content(user))
    logger.info("Wrote unit to %s", tmp_unit)

    print(_SUDO_NOTICE, file=sys.stderr)
    try:
        subprocess.run(
            [
                "sudo",
                "install",
                "-m",
                "644",
                "-o",
                "root",
                "-g",
                "root",
                str(tmp_unit),
                str(_SYSTEMD_UNIT),
            ],
            check=True,
        )
        logger.info("Installed %s", _SYSTEMD_UNIT)

        subprocess.run(
            ["sudo", "systemctl", "daemon-reload"],
            check=True,
        )

        subprocess.run(
            ["sudo", "systemctl", "enable", "voxd"],
            check=True,
        )

        # Unconditional restart. ``enable`` alone will not restart a
        # running service, so on upgrade we would leak the old binary
        # until reboot. ``restart`` starts it if stopped and cycles it
        # if running — exactly the semantics we want here.
        subprocess.run(
            ["sudo", "systemctl", "restart", "voxd"],
            check=True,
        )
        logger.info("Enabled and restarted voxd.service")
    finally:
        try:
            tmp_unit.unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not remove tmp unit %s", tmp_unit)


def _systemd_uninstall() -> None:
    if _SYSTEMD_UNIT.exists():
        print(_SUDO_NOTICE, file=sys.stderr)
        subprocess.run(
            ["sudo", "systemctl", "disable", "--now", "voxd"],
            check=False,  # may already be stopped
        )
        subprocess.run(
            ["sudo", "rm", "-f", str(_SYSTEMD_UNIT)],
            check=True,
        )
        subprocess.run(
            ["sudo", "systemctl", "daemon-reload"],
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
    """Install voxd as a system service. Returns a status message.

    Must be run as a normal user, not as root or under ``sudo``. The
    command prompts for your sudo password itself when it needs to
    install the system service unit; running it under ``sudo``
    instead would cause all per-user state to be created under
    ``/root/.punt-labs/vox/`` and the generated systemd unit to
    specify ``User=root`` — both wrong. Copilot 3048295090 on PR #162.

    Runs as the invoking user. Per-user state under
    ``~/.punt-labs/vox/`` — keys, logs, runtime state, cache — is
    created with normal user permissions. The system service file
    (``/etc/systemd/system/voxd.service`` or
    ``/Library/LaunchDaemons/com.punt-labs.voxd.plist``) is placed via
    a small set of ``sudo`` subprocess calls; that is the only
    privileged work.
    """
    # Refuse to run as root. ``os.geteuid`` is POSIX-only but so is
    # every platform we install on.
    if os.geteuid() == 0:
        msg = (
            "vox daemon install must be run as your normal user, not root "
            "or sudo. vox will prompt for your sudo password when it needs "
            "to install the system service unit. Re-run without sudo:\n\n"
            "    vox daemon install\n"
        )
        raise SystemExit(msg)

    plat = detect_platform()
    user = getpass.getuser()
    args = _voxd_exec_args()

    # Create per-user state directories as the invoking user.
    state_root = _ensure_user_dirs()

    # Write provider keys into the user's state dir. Normal user
    # permissions — no chown, no fd tricks.
    keys_path = _paths_keys_env_file()
    _write_keys_env(dict(os.environ), keys_path)
    logger.info("Wrote provider keys to %s", keys_path)

    # Pre-flight: kill any stale voxd holding the port. This runs
    # before the platform-specific install path so that ``launchctl
    # load`` / ``systemctl restart`` does not race against a leftover
    # process bound to ``DEFAULT_PORT``. The platform-specific install
    # paths themselves are kept narrow — exactly four ``sudo``
    # subprocess calls each — so this cleanup happens here as the
    # user, not inside the privileged section.
    _ensure_port_free()

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
        f"  State:   {state_root}",
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
