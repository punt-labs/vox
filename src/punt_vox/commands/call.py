"""The ``vox call`` group for voxd's voice-first call and hook context."""

from __future__ import annotations

from typing import TYPE_CHECKING, final

from punt_vox.commands.call_context import CallContextCommand
from punt_vox.commands.voice_call_cli import VoiceCallCli
from punt_vox.commands.voice_call_status_cli import CallStatusCli

if TYPE_CHECKING:
    import typer

    from punt_vox.cli_io import OutputFlags
    from punt_vox.output_formatter import OutputFormatter

__all__ = ["CallCli"]


@final
class CallCli:
    """The ``vox call`` verbs, as bound methods registered on a Typer app.

    Methods, not decorator-wrapped local functions, so each is a real,
    referenced attribute -- avoiding the "defined but never read" false
    positive a locally-scoped ``@app.command`` closure triggers, the same
    shape :class:`~punt_vox.cli_rec.RecCli` already uses for ``vox rec``.
    """

    __slots__ = ()

    @staticmethod
    def build_app(
        formatter: OutputFormatter | None = None, flags: OutputFlags | None = None
    ) -> typer.Typer:
        """Return the ``vox call`` Typer group with bound methods (no wrappers)."""
        app = VoiceCallCli.build_app()
        app.command("status")(CallStatusCli(formatter, flags).status)
        app.command("context")(CallContextCommand.show)
        return app
