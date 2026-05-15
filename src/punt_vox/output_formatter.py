"""CLI output formatting strategy."""

from __future__ import annotations

import json
from typing import Self

import typer

__all__ = ["OutputFormatter"]


class OutputFormatter:
    """Emit CLI output as JSON or human-readable text."""

    __slots__ = ("_json", "_quiet")

    _json: bool
    _quiet: bool

    def __new__(cls, *, json_output: bool = False, quiet: bool = False) -> Self:
        self = super().__new__(cls)
        self._json = json_output
        self._quiet = quiet
        return self

    def set_json(self, *, value: bool) -> None:
        """Enable or disable JSON output mode."""
        self._json = value

    def set_quiet(self, *, value: bool) -> None:
        """Enable or disable quiet mode."""
        self._quiet = value

    def emit(self, payload: object, text: str) -> None:
        """Write *payload* as JSON or *text* as plain output."""
        if self._json:
            typer.echo(json.dumps(payload))
        elif not self._quiet:
            typer.echo(text)
