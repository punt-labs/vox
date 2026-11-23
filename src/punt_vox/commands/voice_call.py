"""``vox call`` without ``--context``: a thin client of voxd's Mode B call RPCs.

voxd owns the ElevenLabs conversation, the boss tools and the workers they
start, so the call and its workers outlive this process. This client starts
the call and hands it to ``CallFollower`` to show until it ends.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Self, final

import typer

from punt_vox.client_errors import VoxdConnectionError, VoxdProtocolError
from punt_vox.commands.voice_call_follow import CallFollower
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
            typer.Exit: Exit 1 when voxd refuses or the call fails; see
                ``CallFollower.until_end`` for a lost status or an interrupt,
                and ``CallFollower.hang_up`` for Ctrl-C while the call starts.
        """
        fields: dict[str, object] = {}
        if script is not None:
            fields["script"] = VoiceCallScript.read(script).wire()
        follower = CallFollower(self._control, self._poll_s)
        try:
            started = self._begin(fields)
        except KeyboardInterrupt:
            # voxd may already be dialing the call this request asked for.
            follower.finish(follower.hang_up())
            return
        follower.finish(follower.until_end(started))

    def stop(self) -> None:
        """Stop the running call; refuse when no call is running.

        Raises:
            typer.BadParameter: When no Mode B call is running.
        """
        status = CallStatusView(
            self._run(self._control.request("status", timeout=_CONTROL_TIMEOUT_S))
        )
        if not status.running:
            raise typer.BadParameter("no call is active")
        self._run(self._control.request("stop", timeout=_CONTROL_TIMEOUT_S))
        typer.echo("Stopping the call.")

    def status(self) -> None:
        """Print the current call's status line and transcript so far."""
        view = CallStatusView(
            self._run(self._control.request("status", timeout=_CONTROL_TIMEOUT_S))
        )
        typer.echo(view.line())
        for line in view.report():
            typer.echo(line)

    def _begin(self, fields: dict[str, object]) -> CallStatusView:
        """Send ``call_start``; a call that failed to connect reports its trace."""
        try:
            return CallStatusView(
                asyncio.run(
                    self._control.request("start", timeout=_START_TIMEOUT_S, **fields)
                )
            )
        except VoxdConnectionError as exc:
            typer.echo(f"Call unavailable: {exc}", err=True)
            raise typer.Exit(1) from None
        except VoxdProtocolError as exc:
            typer.echo(f"Call unavailable: {exc}", err=True)
            self._report_failed_start()
            raise typer.Exit(1) from None

    def _report_failed_start(self) -> None:
        """Print the report of a call voxd refused because it failed to connect.

        The refusal is already on stderr, so a status that cannot be read adds
        nothing and is left out.
        """
        try:
            status = CallStatusView(
                asyncio.run(self._control.request("status", timeout=_CONTROL_TIMEOUT_S))
            )
        except (VoxdConnectionError, VoxdProtocolError):
            return
        if status.failed:
            CallFollower.finish(status)

    @staticmethod
    def _run(request: Coroutine[object, object, object]) -> object:
        """Run one coroutine, turning an unreachable or refusing daemon into exit 1."""
        try:
            return asyncio.run(request)
        except (VoxdConnectionError, VoxdProtocolError) as exc:
            typer.echo(f"Call unavailable: {exc}", err=True)
            raise typer.Exit(1) from None
