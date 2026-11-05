"""The voxd systemd unit: the env it captures, its text, and its bind.

Holds no systemd knowledge beyond the unit file format: it authors the text
the backend installs and reads back the ``VOXD_BIND`` it wrote. Every
``systemctl`` and ``sudo`` call belongs to :class:`SystemdBackend`.
"""

from __future__ import annotations

import logging
import os
import pwd
import shlex
import textwrap
from typing import TYPE_CHECKING, ClassVar, Self, final

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = ["SystemdUnit"]


@final
class SystemdUnit:
    """Own the voxd unit file's content and read its installed bind."""

    __slots__ = ("_exec_args_fn", "_path")

    _AUDIO_VARS: ClassVar[tuple[str, ...]] = (
        "XDG_RUNTIME_DIR",
        "PULSE_SERVER",
        "DBUS_SESSION_BUS_ADDRESS",
    )
    _BIND_PREFIX: ClassVar[str] = 'Environment="VOXD_BIND='

    _path: Path
    _exec_args_fn: Callable[[], list[str]]

    def __new__(cls, path: Path, exec_args_fn: Callable[[], list[str]]) -> Self:
        self = super().__new__(cls)
        self._path = path
        self._exec_args_fn = exec_args_fn
        return self

    @property
    def path(self) -> Path:
        """Return where the unit is installed."""
        return self._path

    def exists(self) -> bool:
        """Return True when the unit file is present."""
        return self._path.exists()

    @staticmethod
    def safe_value(value: str) -> bool:
        """Return True if *value* is safe to embed in an ``Environment=`` line.

        Rejects newlines, double quotes, and backslashes.
        """
        return not any(c in value for c in '\n\r"\\')

    def audio_env_lines(self, user: str) -> list[str]:
        """Build ``Environment=`` lines for audio-related env vars.

        PulseAudio/PipeWire need XDG_RUNTIME_DIR to find the user socket.
        """
        lines: list[str] = []
        for name in self._AUDIO_VARS:
            value = os.environ.get(name)
            if not value:
                continue
            if not self.safe_value(value):
                logger.warning("Skipping %s: value contains unsafe characters", name)
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

    def content(self, user: str) -> str:
        """Generate the unit file content for *user*."""
        exec_start = " ".join(shlex.quote(a) for a in self._exec_args_fn())
        raw_path = os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")
        bind = os.environ.get("VOXD_BIND")
        bind_lines: list[str] = []
        if bind and self.safe_value(bind):
            bind_lines = [f'{self._BIND_PREFIX}{bind.replace("%", "%%")}"']
        env_block = ("\n" + " " * 8).join(
            [
                f'Environment="PATH={raw_path.replace("%", "%%")}"',
                *self.audio_env_lines(user),
                *bind_lines,
            ]
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

    def installed_bind(self) -> str:
        """Return the ``VOXD_BIND`` the installed unit passes to voxd.

        ``""`` when the unit is absent or sets none. The unit is installed mode
        644, so this reads it without sudo. ``%%`` is undone because the unit
        escapes ``%`` that way.
        """
        if not self._path.exists():
            return ""
        for raw in self._path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line.startswith(self._BIND_PREFIX) and line.endswith('"'):
                return line[len(self._BIND_PREFIX) : -1].replace("%%", "%")
        return ""
