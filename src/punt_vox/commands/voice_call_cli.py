"""The ``vox call`` verbs for voxd's Mode B call agent."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Self, final

import typer

from punt_vox.commands.voice_call import VoiceCall
from punt_vox.commands.voice_call_options import VoiceCallOptions

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
_HeadsetOpt = Annotated[
    bool, typer.Option("--headset", help="Declare that you are wearing a headset.")
]
_AecOpt = Annotated[
    bool,
    typer.Option("--aec", help="Declare a system echo-cancelled audio path."),
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
        """Register the bare call and its Mode B subcommands."""
        cli = cls()
        app.callback(invoke_without_command=True)(cli.call)
        app.command("status")(cli.status)
        app.command("teardown")(cli.teardown)

    def call(
        self,
        ctx: typer.Context,
        *,
        script: _ScriptOpt = None,
        headset: _HeadsetOpt = False,
        aec: _AecOpt = False,
    ) -> None:
        """Start voxd's call (mic, or --script audio) and follow it to its end."""
        if ctx.invoked_subcommand is None:
            VoiceCall().start(
                VoiceCallOptions.from_flags(script, headset=headset, aec=aec)
            )
        elif option := next(
            (
                flag
                for flag, used in (
                    ("--script", script is not None),
                    ("--headset", headset),
                    ("--aec", aec),
                )
                if used
            ),
            None,
        ):
            raise typer.BadParameter(
                f"{option} before a subcommand applies only to bare `vox call`",
                param_hint=option,
            )

    def status(self) -> None:
        """Show voxd's call: state, tool calls, transcript so far."""
        VoiceCall().status()

    def teardown(self) -> None:
        """Delete the managed ElevenLabs agent and tools when no call is active."""
        VoiceCall().teardown()
