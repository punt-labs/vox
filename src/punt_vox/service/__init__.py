"""Daemon lifecycle management for ``voxd``."""

from __future__ import annotations

from typing import TYPE_CHECKING

from punt_vox.service.installer import ServiceInstaller
from punt_vox.service.lifecycle import DaemonLifecycle
from punt_vox.service.process import DEFAULT_PORT

if TYPE_CHECKING:
    from punt_vox.service.types import PlatformName

_installer = ServiceInstaller()


def install() -> str:
    """Install voxd as a system service.  Return a status message."""
    return _installer.install()


def uninstall() -> str:
    """Remove voxd system service.  Return a status message."""
    return _installer.uninstall()


def detect_platform() -> PlatformName:
    """Return ``'macos'`` or ``'linux'``.  Raise on unsupported platforms."""
    return ServiceInstaller.detect_platform()


__all__ = [
    "DEFAULT_PORT",
    "DaemonLifecycle",
    "ServiceInstaller",
    "detect_platform",
    "install",
    "uninstall",
]
