"""CLI input/output helpers: output-flag routing and text-segment resolution."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Self

import typer

from punt_vox.logging_config import configure_logging
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
        """Fold one position's flags into the shared formatter and logging."""
        if verbose:
            self._verbose_seen = True
        if quiet:
            self._quiet_seen = True
        # Check the accumulated flags, not this call's: a split like
        # ``vox --verbose status --quiet`` sets each flag in a different position.
        if self._verbose_seen and self._quiet_seen:
            raise typer.BadParameter("--verbose and --quiet are mutually exclusive.")
        if json_output:
            self._formatter.set_json(value=True)
        if quiet:
            self._formatter.set_quiet(value=True)
        configure_logging(stderr_level="DEBUG" if self._verbose_seen else "WARNING")


class TextInput:
    """Resolve CLI text input into synthesis segments.

    Text comes from the positional argument, a ``--from`` JSON file, or stdin
    (when the argument is ``-`` or the input is piped). Empty-input errors go to
    stderr and exit non-zero -- the Unix convention for a usage error.
    """

    __slots__ = ("_formatter",)

    _formatter: OutputFormatter

    def __new__(cls, formatter: OutputFormatter) -> Self:
        self = super().__new__(cls)
        self._formatter = formatter
        return self

    def resolve(self, text: str | None, from_file: Path | None) -> list[str]:
        """Return the text segments for *text*/*from_file*, or exit on empty input.

        Text may come from the argument, from a ``--from`` JSON file (an array of
        strings or ``{text}`` objects), or from stdin -- when TEXT is ``-`` or
        when no argument is given and stdin is not a terminal (a pipe).
        """
        if from_file is not None:
            return self._segments_from_file(from_file)
        if self._should_read_stdin(text):
            return [self._read_stdin()]
        if text is None:
            typer.echo(
                "Error: provide TEXT argument, --from file, or piped stdin.", err=True
            )
            raise typer.Exit(code=1)
        return [text]

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
        """Read and return stripped text from stdin, or exit on empty input."""
        data = sys.stdin.read().strip()
        if not data:
            typer.echo("Error: no text on stdin.", err=True)
            raise typer.Exit(code=1)
        return data

    @staticmethod
    def _segments_from_file(from_file: Path) -> list[str]:
        """Parse a JSON segments file into a list of text strings."""
        try:
            raw = json.loads(from_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise typer.BadParameter("--from file must contain valid JSON.") from exc

        if not isinstance(raw, list):
            raise typer.BadParameter("--from file must contain a JSON array.")

        segments: list[str] = []
        for i, item in enumerate(raw):  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
            seg_text: str
            if isinstance(item, str):
                seg_text = item
            elif isinstance(item, dict):
                seg_text = str(item.get("text") or "")  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
            else:
                raise typer.BadParameter(
                    f"Element {i} must be a string or {{voice, text}} object."
                )

            if seg_text:
                segments.append(seg_text)
        return segments
