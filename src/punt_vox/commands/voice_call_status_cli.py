"""The on-demand ``vox call status`` command."""

from __future__ import annotations

from typing import Annotated, Self, final

import typer

from punt_vox.cli_io import OutputFlags
from punt_vox.client_errors import VoxdConnectionError, VoxdProtocolError
from punt_vox.commands.voice_call import VoiceCall
from punt_vox.commands.voice_call_status import CallStatusView
from punt_vox.output_formatter import OutputFormatter

__all__ = ["CallStatusCli"]

_JsonOutput = Annotated[bool, typer.Option("--json", help="Output JSON.")]
_Verbose = Annotated[
    bool, typer.Option("--verbose", "-v", help="Enable debug logging.")
]
_Quiet = Annotated[
    bool, typer.Option("--quiet", "-q", help="Suppress non-JSON output.")
]


@final
class CallStatusCli:
    """Read one daemon status snapshot and format it for the CLI."""

    __slots__ = ("_flags", "_formatter")
    _flags: OutputFlags
    _formatter: OutputFormatter

    def __new__(cls, output: tuple[OutputFormatter, OutputFlags] | None) -> Self:
        """Use the root's output pair, or create a pair for a standalone group."""
        self = super().__new__(cls)
        self._formatter = output[0] if output is not None else OutputFormatter()
        self._flags = output[1] if output is not None else OutputFlags(self._formatter)
        return self

    def status(
        self,
        *,
        json_output: _JsonOutput = False,
        verbose: _Verbose = False,
        quiet: _Quiet = False,
    ) -> None:
        """Show voxd's call: state, tool calls, transcript so far."""
        self._flags.apply(json_output=json_output, verbose=verbose, quiet=quiet)
        try:
            payload = VoiceCall().status_snapshot()
            view = CallStatusView(payload)
        except (VoxdConnectionError, VoxdProtocolError) as exc:
            self._formatter.error(str(exc), f"Call unavailable: {exc}")
            raise typer.Exit(1) from None
        self._formatter.emit(payload, "\n".join([view.line(), *view.report()]))
