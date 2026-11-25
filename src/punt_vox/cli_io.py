"""Route CLI output flags across command positions."""

from __future__ import annotations

from typing import TYPE_CHECKING, Self

import typer

from punt_vox.logging_config import configure_client_logging

if TYPE_CHECKING:
    from punt_vox.output_formatter import OutputFormatter

__all__ = ["OutputFlags"]


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
        self._validate_flags()
        if json_output:
            self._formatter.set_json(value=True)
        if quiet:
            self._formatter.set_quiet(value=True)
        configure_client_logging(role="cli", verbose=self._verbose_seen)

    def _validate_flags(self) -> None:
        # The check runs against the accumulated flags, not this call's, so a
        # split like ``vox --verbose status --quiet`` -- verbose on the callback,
        # quiet on the command -- cannot slip past by landing one flag per side.
        if self._verbose_seen and self._quiet_seen:
            raise typer.BadParameter("--verbose and --quiet are mutually exclusive.")
