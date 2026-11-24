"""Read-only Claude Desktop registration verdict for vox status."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, final

from punt_vox.desktop_install import DesktopInstaller

if TYPE_CHECKING:
    from pathlib import Path


@final
class DesktopRegistration:
    """Report the registration state without requiring install support."""

    @classmethod
    def read(cls) -> str:
        """Return one of five operator-visible states.

        - ``"registered"``: a ``vox`` entry lives under ``mcpServers``.
        - ``"not registered"``: the config exists but has no such entry.
        - ``"no config"``: the config file does not exist.
        - ``"config unreadable"``: the file could not be read or parsed.
        - ``"platform unsupported"``: vox has no config path for this platform.
        """
        try:
            config_path = DesktopInstaller.config_path()
        except ValueError:
            return "platform unsupported"
        return cls._read_config(config_path)

    @staticmethod
    def _read_config(config_path: Path) -> str:
        try:
            data: object = json.loads(config_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return "no config"
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            return "config unreadable"
        match data:
            case {"mcpServers": {"vox": object()}}:
                return "registered"
            case _:
                return "not registered"
