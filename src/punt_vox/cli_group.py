"""Root command boundary for machine-readable usage errors."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from typing import TYPE_CHECKING, final

import typer
from typer._click.exceptions import UsageError
from typer.core import TyperGroup

from punt_vox.output_formatter import OutputFormatter

if TYPE_CHECKING:
    from typer._click import Context


@final
class VoxGroup(TyperGroup):
    """Report parser failures before command callbacks can apply output flags."""

    _json_requested: bool = False

    @classmethod
    def app(cls) -> typer.Typer:
        """Build the root CLI with its usage-error policy and help conventions."""
        return typer.Typer(
            cls=cls,
            name="vox",
            help="Text-to-speech CLI.",
            no_args_is_help=True,
            context_settings={"help_option_names": ["-h", "--help"]},
        )

    def parse_args(self, ctx: Context, args: list[str]) -> list[str]:
        """Read the output mode before Click can reject an argument."""
        options = args[: args.index("--")] if "--" in args else args
        self._json_requested = "--json" in options
        with self._usage_errors():
            return super().parse_args(ctx, args)

    def invoke(self, ctx: Context) -> object:
        """Include command resolution and child-command parsing in the boundary."""
        with self._usage_errors():
            return super().invoke(ctx)

    @contextmanager
    def _usage_errors(self) -> Generator[None]:
        try:
            yield
        except UsageError as exc:
            if not self._json_requested:
                raise
            OutputFormatter(json_output=True).error(exc.format_message(), "")
            raise typer.Exit(exc.exit_code) from exc
