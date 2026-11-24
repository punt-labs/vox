"""CLI boundary for plugin hook commands."""

from __future__ import annotations

from typing import TYPE_CHECKING, final

import typer

from punt_vox.hook_context_relay import HookContextCommand
from punt_vox.paths import log_dir

if TYPE_CHECKING:
    from collections.abc import Callable

LOG_DIR_COMMAND = "_log-dir"


@final
class HookCli:
    """Build the hook group and attach its internal commands."""

    @staticmethod
    def app(read_input: Callable[[], dict[str, object]]) -> typer.Typer:
        """Create the hook group with its lookup and context relay commands."""
        app = typer.Typer(
            help="Hook dispatchers (called by hook scripts).",
            no_args_is_help=True,
        )
        app.command(LOG_DIR_COMMAND, hidden=True)(HookCli._print_log_dir)
        app.command("context-event", hidden=True)(HookContextCommand(read_input).run)
        return app

    @staticmethod
    def _print_log_dir() -> None:
        typer.echo(log_dir())
