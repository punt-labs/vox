"""Read a file a CLI option names, reporting read failures as usage errors."""

from __future__ import annotations

from typing import TYPE_CHECKING, Self, final

import typer

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["OptionFile"]


@final
class OptionFile:
    """The UTF-8 text file passed to one CLI option, such as ``--from``.

    Every failure is a ``typer.BadParameter`` naming the option, so the CLI
    reports it as a usage error (and as a JSON envelope under ``--json``),
    never a traceback.
    """

    __slots__ = ("_option", "_path")

    _option: str
    _path: Path

    def __new__(cls, path: Path, option: str) -> Self:
        """Create a reader for *path*, named in errors by *option*."""
        self = super().__new__(cls)
        self._path = path
        self._option = option
        return self

    def text(self) -> str:
        """Return the file's text.

        Raises:
            typer.BadParameter: If the file cannot be read or is not UTF-8.
        """
        try:
            return self._path.read_text(encoding="utf-8")
        except OSError as exc:
            msg = f"Cannot read {self._option} file: {exc.strerror or exc}."
            raise typer.BadParameter(msg) from exc
        except UnicodeDecodeError as exc:
            msg = (
                f"{self._option} file is not UTF-8 (byte {exc.start}); "
                "save it as UTF-8."
            )
            raise typer.BadParameter(msg) from exc
