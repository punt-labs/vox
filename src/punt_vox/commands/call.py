"""The ``vox call`` group for voxd's voice-first call and hook context."""

from __future__ import annotations

from typing import Self, final

import typer

from punt_vox.commands.call_context import CallContextCommand
from punt_vox.commands.voice_call import VoiceCall
from punt_vox.commands.voice_call_cli import VoiceCallCli

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

    def __new__(cls) -> Self:
        """Create a call cli."""
        return super().__new__(cls)

    @classmethod
    def build_app(cls) -> typer.Typer:
        """Return the ``vox call`` Typer group with bound methods (no wrappers)."""
        app = typer.Typer(help="Place or manage a Vox call.")
        VoiceCallCli.register(app)
        app.command("stop")(VoiceCall().stop)
        app.command("context")(CallContextCommand.show)
        return app
