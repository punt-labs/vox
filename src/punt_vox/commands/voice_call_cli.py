"""The ``vox call`` verbs for voxd's Mode B call: bare ``vox call`` and ``status``."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Self, final

import typer

from punt_vox.commands.voice_call import VoiceCall

__all__ = ["VoiceCallCli"]

_ScriptOpt = Annotated[
    Path | None,
    typer.Option(
        "--script",
        help=(
            "Dev/CI path for bare `vox call`: a JSON Lines file of recorded "
            'caller turns ({"pcm_path": ..., "pause_s": ...} per line; 16 kHz '
            "mono s16le PCM, relative paths beside the script) that voxd "
            "paces to the agent instead of the microphone."
        ),
    ),
]


@final
class VoiceCallCli:
    """Bound Typer entry points that hand each verb to :class:`VoiceCall`."""

    __slots__ = ()

    def __new__(cls) -> Self:
        """Create the Mode B call verbs."""
        return super().__new__(cls)

    @classmethod
    def register(cls, app: typer.Typer) -> None:
        """Make bare ``vox call`` start voxd's call, and add ``vox call status``."""
        cli = cls()
        app.callback(invoke_without_command=True)(cli.call)
        app.command("status")(cli.status)

    def call(self, ctx: typer.Context, script: _ScriptOpt = None) -> None:
        """Start voxd's call (mic, or --script audio) and follow it to its end."""
        if ctx.invoked_subcommand is None:
            VoiceCall().start(script)
        elif script is not None:
            raise typer.BadParameter(
                "--script before a subcommand applies only to bare `vox call`",
                param_hint="--script",
            )

    def status(self) -> None:
        """Show voxd's call: state, tool calls, transcript so far."""
        VoiceCall().status()
