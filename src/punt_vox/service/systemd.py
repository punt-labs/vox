"""Linux systemd backend for voxd system service."""

from __future__ import annotations

import logging
import os
import pwd
import shlex
import subprocess
import textwrap
from collections.abc import Callable
from pathlib import Path
from typing import Self

from punt_vox.paths import user_state_dir as _paths_user_state_dir
from punt_vox.service.process import ProcessManager

logger = logging.getLogger(__name__)

_SYSTEMD_DIR = Path("/etc/systemd/system")
_SYSTEMD_UNIT = _SYSTEMD_DIR / "voxd.service"

_SUDO_NOTICE = (
    "Installing voxd as a system service. You may be prompted for your sudo password."
)


class SystemdBackend:
    """Install, uninstall, stop, and query voxd under Linux systemd."""

    __slots__ = ("_process_mgr", "_voxd_exec_args_fn")

    _process_mgr: ProcessManager
    _voxd_exec_args_fn: Callable[[], list[str]]

    def __new__(
        cls,
        process_mgr: ProcessManager,
        voxd_exec_args_fn: Callable[[], list[str]],
    ) -> Self:
        self = super().__new__(cls)
        self._process_mgr = process_mgr
        self._voxd_exec_args_fn = voxd_exec_args_fn
        return self

    # ------------------------------------------------------------------
    # Systemd value safety
    # ------------------------------------------------------------------

    @staticmethod
    def safe_systemd_value(value: str) -> bool:
        """Return True if *value* is safe to embed in a systemd Environment= line.

        Rejects newlines, double quotes, and backslashes.
        """
        return not any(c in value for c in '\n\r"\\')

    # ------------------------------------------------------------------
    # Unit content generation
    # ------------------------------------------------------------------

    def audio_env_lines(self, user: str) -> list[str]:
        """Build Environment= lines for audio-related env vars.

        PulseAudio/PipeWire need XDG_RUNTIME_DIR to find the user socket.
        """
        audio_vars = ("XDG_RUNTIME_DIR", "PULSE_SERVER", "DBUS_SESSION_BUS_ADDRESS")
        lines: list[str] = []
        for name in audio_vars:
            value = os.environ.get(name)
            if value:
                if not self.safe_systemd_value(value):
                    logger.warning(
                        "Skipping %s: value contains unsafe characters", name
                    )
                    continue
                lines.append(f'Environment="{name}={value.replace("%", "%%")}"')
        # Deterministic fallback for XDG_RUNTIME_DIR.
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

    def unit_content(self, user: str) -> str:
        """Generate the systemd unit file content for *user*."""
        args = self._voxd_exec_args_fn()
        exec_start = " ".join(shlex.quote(a) for a in args)
        raw_path = os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")
        path_value = raw_path.replace("%", "%%")
        audio_lines = self.audio_env_lines(user)
        bind = os.environ.get("VOXD_BIND")
        bind_lines: list[str] = []
        if bind and self.safe_systemd_value(bind):
            bind_lines = [f'Environment="VOXD_BIND={bind.replace("%", "%%")}"']
        env_block = ("\n" + " " * 8).join(
            [f'Environment="PATH={path_value}"', *audio_lines, *bind_lines]
        )
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

    # ------------------------------------------------------------------
    # Service lifecycle
    # ------------------------------------------------------------------

    def stop(self) -> None:
        """Stop voxd under systemd if running.  Idempotent."""
        if not _SYSTEMD_UNIT.exists():
            return
        subprocess.run(
            ["sudo", "systemctl", "stop", "voxd"],
            check=False,
        )
        logger.info("Stopped any previously-running voxd.service")

    def install(self, user: str) -> None:
        """Install the systemd unit.  Sudo is invoked four times."""
        state_root = _paths_user_state_dir()
        tmp_unit = state_root / "voxd.service.tmp"
        tmp_unit.write_text(self.unit_content(user))
        logger.info("Wrote unit to %s", tmp_unit)

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

    def uninstall(self) -> None:
        """Remove the systemd unit and kill any stale daemon."""
        if _SYSTEMD_UNIT.exists():
            logger.warning(_SUDO_NOTICE)
            subprocess.run(
                ["sudo", "systemctl", "disable", "--now", "voxd"],
                check=False,
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
        self._process_mgr.kill_stale_daemon()

    def status(self) -> bool:
        """Return True if voxd is active under systemd."""
        result = subprocess.run(
            ["systemctl", "is-active", "voxd"],
            capture_output=True,
            text=True,
        )
        return result.stdout.strip() == "active"
