"""Report expected failures at the root CLI boundary."""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING, final

import typer
from typer._click.exceptions import UsageError

from punt_vox.config_text import ConfigEncodingError
from punt_vox.output_formatter import OutputFormatter

if TYPE_CHECKING:
    from collections.abc import Generator


@final
class CliFailures:
    """Map parser and config encoding errors to their CLI representation."""

    @staticmethod
    @contextmanager
    def report(*, json_output: bool) -> Generator[None]:
        """Handle the expected failures while leaving other faults untouched."""
        formatter = OutputFormatter(json_output=json_output)
        try:
            yield
        except UsageError as exc:
            if not json_output:
                raise
            formatter.error(exc.format_message(), "")
            raise typer.Exit(exc.exit_code) from exc
        except ConfigEncodingError as exc:
            message = f"{exc.path} is not valid UTF-8"
            formatter.error(message, f"Error: {message}")
            raise typer.Exit(code=1) from exc
