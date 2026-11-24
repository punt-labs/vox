"""The ``vox call`` verbs for voxd's voice call agent."""

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
            "Dev/CI audio source: a JSON Lines file of recorded "
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
        """Create the voice call verbs."""
        return super().__new__(cls)

    @classmethod
    def build_app(cls) -> typer.Typer:
        """Build a Typer group for the call lifecycle commands."""
        app = typer.Typer(help="Place or manage a Vox call.")
        cli = cls()
        app.callback(invoke_without_command=True)(cli.call)
        app.command("teardown")(cli.teardown)
        app.command("stop")(VoiceCall().stop)
        return app

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
        else:
            self._reject_call_only_options(
                {"--script": script is not None, "--headset": headset, "--aec": aec}
            )

    @staticmethod
    def _reject_call_only_options(options: dict[str, bool]) -> None:
        if option := next((flag for flag, used in options.items() if used), None):
            raise typer.BadParameter(
                f"{option} before a subcommand applies only to bare `vox call`",
                param_hint=option,
            )

    def teardown(self) -> None:
        """Delete the managed ElevenLabs agent and tools when no call is active."""
        VoiceCall().teardown()
