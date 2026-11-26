"""The text that marks every remote resource voxd on this host creates."""

from __future__ import annotations

import contextlib
import os
import secrets
import tempfile
from pathlib import Path
from typing import Final, Self, final

from punt_vox.voxd.voice_agent.fault import ConvAIError

__all__ = ["HostMarker"]

_VOXD: Final = "vox-voice-agent"


@final
class HostMarker:
    """Name voxd and this host, so a delete can prove a resource is ours.

    The host token is random and lives in a private file beside the
    registration, never in the registration. A registration copied from
    another host, or edited to name someone else's resource, therefore
    cannot make this host delete what it did not create.
    """

    __slots__ = ("_file",)
    _file: Path

    def __new__(cls, registration: Path) -> Self:
        """Bind the marker to the host file beside *registration*."""
        self = super().__new__(cls)
        self._file = registration.with_suffix(".host")
        return self

    def text(self) -> str:
        """Return the marker, creating the host token on first use."""
        try:
            token = self._read(self._file)
        except FileNotFoundError:
            token = self._create(self._file)
        return f"[{_VOXD} host={token}]"

    @classmethod
    def _create(cls, target: Path) -> str:
        """Publish a complete token file or none: never a partial one."""
        token = secrets.token_hex(16)
        scratch = ""
        try:
            fd, scratch = tempfile.mkstemp(
                dir=target.parent, prefix=f".{target.name}.", suffix=".tmp"
            )
            try:
                os.write(fd, token.encode())
                os.fsync(fd)
            finally:
                os.close(fd)
            try:
                os.link(scratch, target)  # Fails if another voxd got there first.
            except FileExistsError:
                return cls._read(target)
        except OSError as exc:
            msg = f"Cannot create the Vox host marker ({type(exc).__name__})"
            raise ConvAIError(msg) from None
        finally:
            if scratch:
                with contextlib.suppress(OSError):
                    Path(scratch).unlink()
        return token

    @staticmethod
    def _read(target: Path) -> str:
        try:
            token = target.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            raise
        except (OSError, UnicodeDecodeError) as exc:
            msg = f"Unreadable Vox host marker ({type(exc).__name__})"
            raise ConvAIError(msg) from None
        if not token:
            msg = "Vox host marker is empty; delete it to create a new one"
            raise ConvAIError(msg)
        return token
