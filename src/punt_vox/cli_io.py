"""CLI input/output helpers: output-flag routing and text-segment resolution."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Self

import typer

from punt_vox.logging_config import configure_client_logging
from punt_vox.segment_file import SegmentFile
from punt_vox.speech_stdin import SpeechStdin

if TYPE_CHECKING:
    from pathlib import Path

    from punt_vox.output_formatter import OutputFormatter

__all__ = ["OutputFlags", "TextInput"]


class OutputFlags:
    """Route --json/--verbose/--quiet from both the callback and the command.

    The same flags live on the Typer callback (pre-subcommand) and on the
    state-emitting commands (post-subcommand). Both positions OR together, so
    ``vox --json status`` and ``vox status --json`` select the same mode. The
    accumulated ``_verbose_seen`` drives logging, so a --verbose in either
    position raises the stderr level regardless of where it appears.
    """

    __slots__ = ("_formatter", "_quiet_seen", "_verbose_seen")

    _formatter: OutputFormatter
    _verbose_seen: bool
    _quiet_seen: bool

    def __new__(cls, formatter: OutputFormatter) -> Self:
        """Route output flags through the shared formatter."""
        self = super().__new__(cls)
        self._formatter = formatter
        self._verbose_seen = False
        self._quiet_seen = False
        return self

    def reset(self) -> None:
        """Clear accumulated state so a reused process does not leak prior flags."""
        self._verbose_seen = False
        self._quiet_seen = False
        self._formatter.set_json(value=False)
        self._formatter.set_quiet(value=False)

    def apply(self, *, json_output: bool, verbose: bool, quiet: bool) -> None:
        """Fold one position's flags into the shared formatter and logging.

        Raises:
            typer.BadParameter: If verbose and quiet are both requested.
        """
        self._verbose_seen = self._verbose_seen or verbose
        self._quiet_seen = self._quiet_seen or quiet
        # The check runs against the accumulated flags, not this call's, so a
        # split like ``vox --verbose status --quiet`` -- verbose on the callback,
        # quiet on the command -- cannot slip past by landing one flag per side.
        if self._verbose_seen and self._quiet_seen:
            raise typer.BadParameter("--verbose and --quiet are mutually exclusive.")
        if json_output:
            self._formatter.set_json(value=True)
        if quiet:
            self._formatter.set_quiet(value=True)
        configure_client_logging(role="cli", verbose=self._verbose_seen)


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
        """Return the text segments for *text*/*from_file*, or exit on empty input.

        Text may come from the argument, from a ``--from`` JSON file (an array of
        strings or ``{text}`` objects), or from stdin -- when TEXT is ``-`` or
        when no argument is given and stdin is not a terminal (a pipe).

        Raises:
            typer.Exit: If no text is provided or stdin is empty or not UTF-8.
            typer.BadParameter: If the file cannot be read or its content is invalid.
            OSError: If standard input cannot be read.
        """
        if from_file is not None:
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
        return text == "-" or (text is None and not sys.stdin.isatty())

    def _read_stdin(self) -> str:
        """Read speech stdin with the shared formatter."""
        return SpeechStdin(self._formatter).read()
