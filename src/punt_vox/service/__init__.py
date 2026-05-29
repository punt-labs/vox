"""Daemon lifecycle management for ``voxd``."""
# pyright: reportPrivateUsage=false
# Re-exporting private names from submodules is the whole point of __init__.py.

from __future__ import annotations

from pathlib import Path

from punt_vox.service.installer import ServiceInstaller
from punt_vox.service.keys_env import KeysEnvWriter
from punt_vox.service.launchd import (
    _LAUNCHD_PLIST,
    _OLD_LAUNCHD_PLIST,
    LaunchdBackend,
)
from punt_vox.service.process import (
    DEFAULT_PORT,
    ProcessManager,
)
from punt_vox.service.systemd import (
    _LEGACY_USER_UNIT_RELATIVE,
    _SYSTEMD_DIR,
    _SYSTEMD_UNIT,
    SystemdBackend,
)

# ---------------------------------------------------------------------------
# Module-level singletons and free-function API that callers expect.
# ---------------------------------------------------------------------------

_installer = ServiceInstaller()
_process_mgr = _installer._process_mgr
_launchd = _installer._launchd
_systemd = _installer._systemd


# -- Free-function API (preserves the public interface) --------------------


def install() -> str:
    """Install voxd as a system service.  Return a status message."""
    return _installer.install()


def uninstall() -> str:
    """Remove voxd system service.  Return a status message."""
    return _installer.uninstall()


def is_running() -> bool:
    """Check if the daemon service is currently running."""
    return _installer.is_running()


def detect_platform() -> str:
    """Return ``'macos'`` or ``'linux'``.  Raise on unsupported platforms."""
    return ServiceInstaller.detect_platform()


def read_port_file() -> int | None:
    """Read the daemon port from the port file."""
    return _process_mgr.read_port_file()


def ensure_port_free() -> None:
    """Public API: ensure no daemon is using the default port."""
    _process_mgr.ensure_port_free()


def stop_daemon(plat: str) -> None:
    """Public API: stop the voxd daemon for the given platform."""
    if plat == "macos":
        _launchd.stop()
    else:
        _systemd.stop()


def _legacy_user_unit_path() -> Path:
    """Return the legacy per-user systemd unit path."""
    return _systemd.legacy_user_unit_path()


__all__ = [
    "DEFAULT_PORT",
    "_LAUNCHD_PLIST",
    "_LEGACY_USER_UNIT_RELATIVE",
    "_OLD_LAUNCHD_PLIST",
    "_SYSTEMD_DIR",
    "_SYSTEMD_UNIT",
    "KeysEnvWriter",
    "LaunchdBackend",
    "ProcessManager",
    "ServiceInstaller",
    "SystemdBackend",
    "_legacy_user_unit_path",
    "detect_platform",
    "ensure_port_free",
    "install",
    "is_running",
    "read_port_file",
    "stop_daemon",
    "uninstall",
]
