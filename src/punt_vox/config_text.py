"""The UTF-8 text of one Vox config file."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Self, final

from punt_vox.atomic_file import AtomicFile

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

__all__ = ["ConfigEncodingError", "ConfigText"]


@final
class ConfigEncodingError(UnicodeDecodeError):
    """A Vox config cannot be decoded as UTF-8."""

    __slots__ = ("_path",)

    _path: Path

    def __new__(cls, path: Path, error: UnicodeDecodeError, /) -> Self:
        """Bind the path and initialize the built-in decoder fields."""
        self = super().__new__(cls)
        UnicodeDecodeError.__init__(
            self, error.encoding, error.object, error.start, error.end, error.reason
        )
        self._path = path
        return self

    # Accept the constructor arguments without reinitializing the codec fields.
    __init__: Callable[..., None] = Exception.__init__

    @property
    def path(self) -> Path:
        """Return the affected config file."""
        return self._path


@final
class ConfigText:
    """Read and atomically replace config text without changing its line endings."""

    __slots__ = ("_path",)

    _path: Path

    def __new__(cls, path: Path) -> Self:
        """Bind a config path without accessing the file."""
        self = super().__new__(cls)
        self._path = path
        return self

    @property
    def path(self) -> Path:
        """Return the path to the config text."""
        return self._path

    def read(self) -> str | None:
        """Read UTF-8 text, returning None only for an absent file."""
        try:
            return self._path.read_text(encoding="utf-8", newline="")
        except FileNotFoundError:
            return None
        except UnicodeDecodeError as exc:
            raise ConfigEncodingError(self._path, exc) from exc

    def replace(self, text: str) -> None:
        """Land all or none of the text, respecting existing permissions or umask."""
        if self._path.exists():
            os.close(os.open(self._path, os.O_WRONLY | os.O_CLOEXEC))
            AtomicFile(self._path).replace(text)
            return
        # A new file lands with the current umask, so the caller controls privacy.
        umask = os.umask(0)
        os.umask(umask)
        AtomicFile(self._path).replace(text, mode=0o666 & ~umask)
