"""Read and validate UTF-8 speech from the CLI's standard input."""

from __future__ import annotations

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
        """Read strict UTF-8 and strip surrounding whitespace."""
        stream = typer.get_text_stream("stdin", encoding="utf-8", errors="strict")
        try:
            text = stream.read().strip()
        except UnicodeDecodeError:
            self._reject("speech stdin must be valid UTF-8")
        if not text:
            self._reject("no text on stdin")
        return text

    def _reject(self, message: str) -> NoReturn:
        """Report an input error and stop the CLI."""
        self._formatter.error(message, f"Error: {message}.")
        raise typer.Exit(code=1) from None
