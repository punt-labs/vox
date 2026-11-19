"""Scan a command tree for JSON output intent before callbacks execute."""

from __future__ import annotations

from typing import TYPE_CHECKING, Self

from typer.core import TyperGroup

from punt_vox.cli_usage_parser import UsageParser

if TYPE_CHECKING:
    from typer._click import Command, Context


class JsonRequest:
    """Follow raw parser results without conversions or option callbacks."""

    _command: Command

    def __new__(cls, command: Command) -> Self:
        """Bind the root command supplying the global JSON option."""
        self = super().__new__(cls)
        self._command = command
        return self

    def requested(self, ctx: Context, args: list[str]) -> bool:
        """Return whether this invocation requests machine-readable output."""
        command = self._command
        json_option = next(
            p for p in command.get_params(ctx) if p.name == "json_output"
        )
        while True:
            scan = command.context_class(
                command, parent=ctx, ignore_unknown_options=True
            )
            requested, rest = UsageParser(command, scan, json_option).parse(args)
            if requested:
                return True
            if not isinstance(command, TyperGroup) or not rest:
                return False
            child = command.get_command(scan, rest[0])
            if child is None:
                scan = command.context_class(
                    command,
                    parent=ctx,
                    ignore_unknown_options=True,
                    allow_interspersed_args=True,
                )
                requested, _ = UsageParser(command, scan, json_option).parse(args)
                return requested
            command, ctx, args = child, scan, rest[1:]
