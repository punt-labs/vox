"""Read a per-call API key from a ``--api-key-file`` path."""

from __future__ import annotations

from typing import TYPE_CHECKING, Self

import typer

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["ApiKeyFile"]


class ApiKeyFile:
    """A file holding one per-call provider API key.

    Every rejection is a ``typer.BadParameter`` so the CLI reports it as a
    usage error (and as a JSON envelope under ``--json``), never a traceback.
    """

    __slots__ = ("_path",)

    _path: Path

    def __new__(cls, path: Path) -> Self:
        """Create a reader for the key file at *path*."""
        self = super().__new__(cls)
        self._path = path
        return self

    def read(self) -> str:
        """Return the key with surrounding whitespace stripped.

        Rejects missing paths, non-files, non-UTF-8 content, and empty
        files. Warns when file permissions are too broad (anything beyond
        0600).
        """
        if not self._path.is_file():
            msg = f"--api-key-file: {self._path} is not a file"
            raise typer.BadParameter(msg)
        self._warn_if_shared()
        value = self._decode().strip()
        if not value:
            msg = f"--api-key-file: {self._path} is empty"
            raise typer.BadParameter(msg)
        return value

    def _warn_if_shared(self) -> None:
        """Warn on stderr when group or other permission bits are set."""
        mode = self._path.stat().st_mode
        if mode & 0o077:
            typer.echo(
                f"warning: --api-key-file: {self._path} is accessible to group "
                f"or other users (mode {oct(mode & 0o777)}). Run "
                f"'chmod 600 {self._path}' to tighten permissions.",
                err=True,
            )

    def _decode(self) -> str:
        """Return the file's text, rejecting bytes that are not UTF-8."""
        try:
            return self._path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            # The decode error's message quotes the offending key byte; keep
            # it out of the user-facing text.
            msg = f"--api-key-file: {self._path} is not valid UTF-8"
            raise typer.BadParameter(msg) from exc
