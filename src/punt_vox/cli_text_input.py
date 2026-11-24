"""Resolve speech input from a positional argument, a JSON file, or stdin."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Self, final

import typer

from punt_vox.segment_file import SegmentFile
from punt_vox.speech_stdin import SpeechStdin

if TYPE_CHECKING:
    from pathlib import Path

    from punt_vox.output_formatter import OutputFormatter


@final
class TextInput:
    """Resolve CLI text input into synthesis segments.

    Text comes from the positional argument, a ``--from`` JSON file, or stdin
    (when the argument is ``-`` or the input is piped). Empty-input errors go to
    stderr and exit non-zero -- the Unix convention for a usage error.
    """

    __slots__ = ("_formatter",)

    _formatter: OutputFormatter

    def __new__(cls, formatter: OutputFormatter) -> Self:
        """Create a text-input resolver using the shared formatter."""
        self = super().__new__(cls)
        self._formatter = formatter
        return self

    def resolve(self, text: str | None, from_file: Path | None) -> list[str]:
        """Resolve exactly one speech input source into text segments.

        Text may come from the argument, from a ``--from`` JSON file (an array of
        strings or ``{text}`` objects), or from stdin -- when TEXT is ``-`` or
        when no argument is given and stdin is not a terminal (a pipe).

        Raises:
            typer.Exit: If no text is provided or stdin is empty or not UTF-8.
            typer.BadParameter: If TEXT and --from are combined or the file is invalid.
            OSError: If standard input cannot be read.
        """
        if from_file is not None:
            if text is not None:
                raise typer.BadParameter("TEXT and --from are mutually exclusive.")
            return SegmentFile(from_file).segments()
        if self._should_read_stdin(text):
            return [self._read_stdin()]
        if text is None:
            self._formatter.error(
                "provide TEXT argument, --from file, or piped stdin",
                "Error: provide TEXT argument, --from file, or piped stdin.",
            )
            raise typer.Exit(code=1)
        return [text]

    @staticmethod
    def prepare_shared_stdin(
        text: str | None, from_file: Path | None, *, api_key_stdin: bool
    ) -> None:
        """Set up speech stdin before a piped API key reads from it."""
        if api_key_stdin and from_file is None and TextInput._should_read_stdin(text):
            SpeechStdin.prepare()

    @staticmethod
    def _should_read_stdin(text: str | None) -> bool:
        """Return whether text should be read from stdin.

        True when the caller passes ``-`` explicitly, or gives no argument while
        stdin is a pipe (not a terminal) -- the Unix convention for composing in
        a pipeline. An interactive shell with no argument is left to the caller's
        usage error rather than blocking on a terminal read.
        """
        return not sys.stdin.isatty() if text is None else text == "-"

    def _read_stdin(self) -> str:
        """Read speech stdin with the shared formatter."""
        return SpeechStdin(self._formatter).read()
