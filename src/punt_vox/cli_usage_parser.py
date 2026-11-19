"""Parse output intent without executing callbacks or converting values."""

from __future__ import annotations

from typing import TYPE_CHECKING, Self, final

from typer._click.exceptions import BadOptionUsage, UsageError
from typer.core import TyperOption

if TYPE_CHECKING:
    from typer._click import Command, Context, Parameter


@final
class UsageParser:
    """Recover from malformed flag assignments only after the parser rejects them."""

    __slots__ = ("_command", "_context", "_json_option")

    _command: Command
    _context: Context
    _json_option: Parameter

    def __new__(
        cls, command: Command, context: Context, json_option: Parameter
    ) -> Self:
        """Create a usage parser."""
        self = super().__new__(cls)
        self._command = command
        self._context = context
        self._json_option = json_option
        return self

    def parse(self, args: list[str]) -> tuple[bool, list[str]]:
        """Return JSON-output intent and arguments, tolerating malformed flags."""
        parser = self._command.make_parser(self._context)
        self._json_option.add_to_parser(parser, self._context)
        pending = args.copy()
        while True:
            remaining = pending.copy()
            try:
                options, rest, _ = parser.parse_args(remaining)
            except UsageError as exc:
                index = len(pending) - len(remaining) - 1
                if self._invalid_flag(exc, pending, index):
                    del pending[index]
                    continue
                self._context.resilient_parsing = True
                options, rest, _ = parser.parse_args(pending.copy())
            return bool(options.get("json_output")), rest

    def _invalid_flag(self, exc: UsageError, args: list[str], index: int) -> bool:
        if not isinstance(exc, BadOptionUsage) or not 0 <= index < len(args):
            return False
        name, separator, _ = args[index].partition("=")
        if not separator or name != exc.option_name:
            return False
        return name in self._flag_names()

    def _flag_names(self) -> set[str]:
        return {
            name
            for parameter in [
                *self._command.get_params(self._context),
                self._json_option,
            ]
            if isinstance(parameter, TyperOption) and parameter.is_flag
            for name in [*parameter.opts, *parameter.secondary_opts]
        }
