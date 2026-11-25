"""Read and validate UTF-8 speech from the CLI's standard input."""

from __future__ import annotations

import sys
from io import TextIOWrapper
from typing import TYPE_CHECKING, NoReturn, Self, final

import typer

if TYPE_CHECKING:
    from punt_vox.output_formatter import OutputFormatter


@final
class SpeechStdin:
    """Own speech stdin decoding and report failures in the selected format."""

    __slots__ = ("_formatter",)

    _formatter: OutputFormatter

    def __new__(cls, formatter: OutputFormatter) -> Self:
        """Create a reader for speech stdin."""
        self = super().__new__(cls)
        self._formatter = formatter
        return self

    def read(self) -> str:
        """Read the current stdin buffer and reject text that is not UTF-8."""
        self.prepare()
        try:
            text = sys.stdin.read().strip()
            text.encode("utf-8")
        except UnicodeError:
            self._reject("speech stdin must be valid UTF-8")
        if not text:
            self._reject("no text on stdin")
        return text

    @staticmethod
    def prepare() -> None:
        """Set the UTF-8 decoder in place before the first stdin read."""
        stream = sys.stdin
        if isinstance(stream, TextIOWrapper) and (stream.encoding, stream.errors) != (
            "utf-8",
            "surrogateescape",
        ):
            stream.reconfigure(encoding="utf-8", errors="surrogateescape")

    def _reject(self, message: str) -> NoReturn:
        """Report an input error and stop the CLI."""
        self._formatter.error(message, f"Error: {message}.")
        raise typer.Exit(code=1) from None
