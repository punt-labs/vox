"""Top-level service installer composing all backends."""

from __future__ import annotations

import getpass
import logging
import os
import platform
import sys
from pathlib import Path
from typing import Self

from punt_vox.paths import (
    ensure_user_dirs as _paths_ensure_user_dirs,
    keys_env_file as _paths_keys_env_file,
    user_state_dir as _paths_user_state_dir,
)
from punt_vox.service.keys_env import KeysEnvWriter
from punt_vox.service.launchd import (
    _LAUNCHD_PLIST,  # pyright: ignore[reportPrivateUsage]
    _OLD_LAUNCHD_PLIST,  # pyright: ignore[reportPrivateUsage]
    LaunchdBackend,
)
from punt_vox.service.process import DEFAULT_PORT, ProcessManager
from punt_vox.service.systemd import (
    _SYSTEMD_UNIT,  # pyright: ignore[reportPrivateUsage]
    SystemdBackend,
)

logger = logging.getLogger(__name__)

# Sudo notice -- Linux only (macOS LaunchAgent installs need no sudo).
_SUDO_NOTICE = (
    "Installing voxd as a system service. You may be prompted for your sudo password."
)


class ServiceInstaller:
    """Compose ProcessManager, KeysEnvWriter, and platform backends."""

    __slots__ = (
        "_keys_writer",
        "_launchd",
        "_process_mgr",
        "_systemd",
    )

    _keys_writer: KeysEnvWriter
    _launchd: LaunchdBackend
    _process_mgr: ProcessManager
    _systemd: SystemdBackend

    def __new__(cls) -> Self:
        self = super().__new__(cls)
        self._process_mgr = ProcessManager()
        self._keys_writer = KeysEnvWriter()
        self._launchd = LaunchdBackend(self._process_mgr, _voxd_exec_args)
        self._systemd = SystemdBackend(self._process_mgr, _voxd_exec_args)
        return self

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _ensure_user_dirs() -> Path:
        """Create per-user state directories under ``~/.punt-labs/vox``."""
        state_root = _paths_user_state_dir()
        _paths_ensure_user_dirs(state_root)
        logger.info("Ensured directory tree under %s", state_root)
        return state_root

    # ------------------------------------------------------------------
    # Platform detection
    # ------------------------------------------------------------------

    @staticmethod
    def detect_platform() -> str:
        """Return ``'macos'`` or ``'linux'``.  Raise on unsupported platforms."""
        system = platform.system()
        if system == "Darwin":
            return "macos"
        if system == "Linux":
            return "linux"
        msg = (
            f"Unsupported platform: {system}. "
            "vox daemon install supports macOS and Linux."
        )
        raise SystemExit(msg)

    # ------------------------------------------------------------------
    # Install
    # ------------------------------------------------------------------

    def _install_darwin(self) -> bool:
        """Run the macOS install path.  Return True if running.

        Detects whether an old LaunchDaemon plist exists at
        ``/Library/LaunchDaemons/com.punt-labs.voxd.plist`` and runs
        the one-time migration if so.  Fresh installs need no sudo.
        """
        if _OLD_LAUNCHD_PLIST.exists():
            logger.warning(
                "Migrating voxd from LaunchDaemon to LaunchAgent "
                "(one sudo prompt to remove old system service)..."
            )
            self._launchd.migrate_from_daemon()
        else:
            self._launchd.stop()
            self._process_mgr.ensure_port_free()
            self._launchd.install()
        return self._launchd.status()

    def _install_linux(self, user: str) -> bool:
        """Run the Linux install path.  Return True if running."""
        self._systemd.cleanup_stale_user_unit()
        self._systemd.stop()
        self._process_mgr.ensure_port_free()
        self._systemd.install(user)
        return self._systemd.status()

    def install(self) -> str:
        """Install voxd as a system service.  Return a status message.

        Must be run as a normal user, not as root or under ``sudo``.
        """
        if os.geteuid() == 0:
            msg = (
                "vox daemon install must run as your normal user, not root. "
                "LaunchAgents install to your home directory and cannot "
                "function under root. Re-run without sudo:\n\n"
                "    vox daemon install\n"
            )
            raise SystemExit(msg)

        plat = self.detect_platform()
        user = getpass.getuser()
        args = _voxd_exec_args()

        state_root = self._ensure_user_dirs()

        keys_path = _paths_keys_env_file()
        self._keys_writer.write(dict(os.environ), keys_path)
        logger.info("Wrote provider keys to %s", keys_path)

        if plat == "macos":
            running = self._install_darwin()
        else:
            logger.warning(_SUDO_NOTICE)
            running = self._install_linux(user)

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

    # ------------------------------------------------------------------
    # Uninstall
    # ------------------------------------------------------------------

    def uninstall(self) -> str:
        """Remove voxd system service.  Return a status message."""
        plat = self.detect_platform()
        if plat == "macos":
            self._launchd.uninstall()
            path = _LAUNCHD_PLIST
        else:
            self._systemd.uninstall()
            path = _SYSTEMD_UNIT
        return f"voxd daemon uninstalled. Removed {path}."

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def is_running(self) -> bool:
        """Check if the daemon service is currently running."""
        plat = self.detect_platform()
        if plat == "macos":
            return self._launchd.status()
        return self._systemd.status()


# ---------------------------------------------------------------------------
# Module-level helper used by backends (avoids circular dependency)
# ---------------------------------------------------------------------------


def _voxd_exec_args() -> list[str]:
    """Return the command to invoke ``voxd``.

    Resolves ``voxd`` relative to ``sys.executable`` so the systemd unit
    always runs the binary from the same distribution that provided
    ``vox``.
    """
    voxd_path = Path(sys.executable).parent / "voxd"
    if not voxd_path.is_file():
        msg = (
            f"voxd binary not found at {voxd_path}. "
            "Reinstall punt-vox (uv tool install punt-vox or pip install punt-vox)."
        )
        raise SystemExit(msg)
    if not os.access(voxd_path, os.X_OK):
        msg = (
            f"voxd at {voxd_path} exists but is not executable. "
            "Reinstall punt-vox (uv tool install punt-vox or pip install punt-vox)."
        )
        raise SystemExit(msg)
    return [str(voxd_path), "--port", str(DEFAULT_PORT)]
