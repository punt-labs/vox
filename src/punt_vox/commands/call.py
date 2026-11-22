"""``vox call`` -- a live voice call, with one of two call agents.

Bare ``vox call`` starts the Mode B call voxd owns
(:class:`~.voice_call_cli.VoiceCallCli`): an ElevenLabs agent that delegates
coding to persistent workers, with ``vox call status`` beside it.
``vox call start --context`` is the read-only pi call agent
(:class:`~.pi_call_cli.PiCallCli`), kept reachable unchanged until Mode B
retires it. ``vox call stop`` (:class:`~.call_hangup.CallHangup`) hangs up
whichever is running.
"""

from __future__ import annotations

from typing import Self, final

import typer

from punt_vox.commands.call_context import CallContextCommand
from punt_vox.commands.call_hangup import CallHangup
from punt_vox.commands.pi_call_cli import PiCallCli
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
        app = typer.Typer(
            help=(
                "A live voice call. Bare `vox call` talks to voxd's agent, "
                "which delegates coding; `vox call start --context` is the "
                "read-only pi call agent."
            ),
        )
        VoiceCallCli.register(app)
        app.command("start")(PiCallCli().start)
        app.command("stop")(CallHangup.stop)
        app.command("context")(CallContextCommand.show)
        return app
