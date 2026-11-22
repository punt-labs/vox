"""``vox call`` without ``--context``: a thin client of voxd's Mode B call RPCs.

voxd owns the ElevenLabs conversation, the boss tools and the workers they
start, so the call and its workers outlive this process. This client starts
the call, shows one live status line until it ends, and stops it on Ctrl-C.
"""

from __future__ import annotations

import asyncio
import sys
from typing import TYPE_CHECKING, Self, cast, final

import typer

from punt_vox.client_errors import VoxdConnectionError, VoxdProtocolError
from punt_vox.commands.voice_call_script import VoiceCallScript
from punt_vox.commands.voice_call_status import CallStatusView
from punt_vox.daemon_control import DaemonControl

if TYPE_CHECKING:
    from collections.abc import Coroutine
    from pathlib import Path

__all__ = ["VoiceCall"]

# call_start waits for the provider to connect and the script to load in voxd.
_START_TIMEOUT_S = 60.0
_CONTROL_TIMEOUT_S = 30.0
_POLL_INTERVAL_S = 0.5


@final
class VoiceCall:
    """Start, follow, inspect and stop the one daemon-owned Mode B call."""

    __slots__ = ("_control", "_poll_s")
    _control: DaemonControl
    _poll_s: float

    def __new__(cls, poll_s: float = _POLL_INTERVAL_S) -> Self:
        """Bind the ``call_*`` RPC family; *poll_s* paces the live status line."""
        self = super().__new__(cls)
        self._control = DaemonControl("call")
        self._poll_s = poll_s
        return self

    def start(self, script: Path | None) -> None:
        """Start a call (mic, or *script*'s recorded turns) and follow it to its end.

        Raises:
            typer.Exit: Exit 1 when voxd refuses or the call fails.
        """
        fields: dict[str, object] = {}
        if script is not None:
            fields["script"] = VoiceCallScript.read(script).wire()
        try:
            started = self._run(self._request("start", _START_TIMEOUT_S, **fields))
            ended = cast(
                "CallStatusView", self._run(self._follow(CallStatusView(started)))
            )
        except KeyboardInterrupt:
            ended = CallStatusView(self._run(self._request("stop", _CONTROL_TIMEOUT_S)))
        self._finish(ended)

    def stop(self) -> None:
        """Stop the running call; refuse when no call is running.

        Raises:
            typer.BadParameter: When no Mode B call is running.
        """
        status = CallStatusView(self._run(self._request("status", _CONTROL_TIMEOUT_S)))
        if not status.running:
            raise typer.BadParameter("no call is active")
        self._run(self._request("stop", _CONTROL_TIMEOUT_S))
        typer.echo("Stopping the call.")

    def status(self) -> None:
        """Print the current call's status line and transcript so far."""
        view = CallStatusView(self._run(self._request("status", _CONTROL_TIMEOUT_S)))
        typer.echo(view.line())
        for line in view.report():
            typer.echo(line)

    async def _request(
        self, operation: str, timeout: float, **fields: object
    ) -> object:
        """Send one ``call_<operation>`` RPC."""
        return await self._control.request(operation, timeout=timeout, **fields)

    async def _follow(self, view: CallStatusView) -> CallStatusView:
        """Poll ``call_status``, echoing the status line each time it changes."""
        shown = ""
        while True:
            line = view.line()
            if line != shown:
                self._show(line)
                shown = line
            if not view.running:
                return view
            await asyncio.sleep(self._poll_s)
            view = CallStatusView(await self._request("status", _CONTROL_TIMEOUT_S))

    @staticmethod
    def _show(line: str) -> None:
        """Rewrite the status line in place on a terminal, else print it."""
        if sys.stdout.isatty():
            typer.echo(f"\r\x1b[K{line}", nl=False)
        else:
            typer.echo(line)

    @staticmethod
    def _finish(view: CallStatusView) -> None:
        """Print the end-of-call report; a failed call exits 1."""
        if sys.stdout.isatty():
            typer.echo("")
        for line in view.report():
            typer.echo(line)
        if view.failed:
            raise typer.Exit(1)

    @staticmethod
    def _run(request: Coroutine[object, object, object]) -> object:
        """Run one coroutine, turning an unreachable or refusing daemon into exit 1."""
        try:
            return asyncio.run(request)
        except (VoxdConnectionError, VoxdProtocolError) as exc:
            typer.echo(f"Call unavailable: {exc}", err=True)
            raise typer.Exit(1) from None
