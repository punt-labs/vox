"""Root command boundary for machine-readable usage errors."""

from __future__ import annotations

from typing import TYPE_CHECKING, final

import typer
from typer._click.exceptions import UsageError
from typer.core import TyperGroup

from punt_vox import usage
from punt_vox.cli_failures import CliFailures
from punt_vox.cli_json_request import JsonRequest
from punt_vox.commands.worker import WorkerCli

if TYPE_CHECKING:
    from contextlib import AbstractContextManager

    from typer._click import Command, Context


@final
class VoxGroup(TyperGroup):
    """Report parser failures before command callbacks can apply output flags."""

    _json_requested: bool = False

    @staticmethod
    def register_workers(app: typer.Typer) -> None:
        """Attach the hidden daemon worker control group."""
        app.add_typer(WorkerCli.build_app(), name="worker", hidden=True)

    @classmethod
    def app(cls) -> typer.Typer:
        """Build the root CLI with its usage-error policy and help conventions."""
        return typer.Typer(
            cls=cls,
            name="vox",
            no_args_is_help=True,
            context_settings={"help_option_names": ["-h", "--help"]},
        )

    def parse_args(self, ctx: Context, args: list[str]) -> list[str]:
        """Read the output mode before Click can reject an argument."""
        self._json_requested = JsonRequest(self).requested(ctx, args)
        with self._failure_boundary():
            return super().parse_args(ctx, args)

    def invoke(self, ctx: Context) -> object:
        """Include command resolution and child-command parsing in the boundary."""
        with self._failure_boundary():
            return super().invoke(ctx)

    def _failure_boundary(self) -> AbstractContextManager[None]:
        """Use the same output mode for parser and runtime failures."""
        return CliFailures.report(json_output=self._json_requested)

    def resolve_command(
        self, ctx: Context, args: list[str]
    ) -> tuple[str | None, Command | None, list[str]]:
        """Reject an empty group before Typer renders its implicit help.

        Raises:
            UsageError: If JSON output is requested without a subcommand.
        """
        usage.register("cli", lambda: self.commands)
        ctx.with_resource(usage.command_span(next(iter(args), "other")))
        name, command, rest = super().resolve_command(ctx, args)
        if (
            self._json_requested
            and isinstance(command, TyperGroup)
            and command.no_args_is_help
            and not rest
        ):
            raise UsageError(f"Missing command for '{name}'.", ctx=ctx)
        return name, command, rest
