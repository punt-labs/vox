"""The text that marks every remote resource voxd on this host creates."""

from __future__ import annotations

import os
import secrets
from typing import TYPE_CHECKING, Final, Self, final

from punt_vox.voxd.voice_agent.fault import ConvAIError

if TYPE_CHECKING:
    from pathlib import Path

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
        return f"[{_VOXD} host={self._token()}]"

    def _token(self) -> str:
        try:
            return self._read()
        except FileNotFoundError:
            pass
        try:
            fd = os.open(self._file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return self._read()  # Another voxd created it first.
        except OSError as exc:
            msg = f"Cannot create the Vox host marker ({type(exc).__name__})"
            raise ConvAIError(msg) from None
        token = secrets.token_hex(16)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(token)
        return token

    def _read(self) -> str:
        try:
            token = self._file.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError) as exc:
            if isinstance(exc, FileNotFoundError):
                raise
            msg = f"Unreadable Vox host marker ({type(exc).__name__})"
            raise ConvAIError(msg) from None
        if not token:  # A creator that died between open and write.
            msg = "Vox host marker is empty; delete it to create a new one"
            raise ConvAIError(msg)
        return token
