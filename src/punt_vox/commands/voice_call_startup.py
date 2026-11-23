"""Dial voxd's Mode B call and report failures before live status polling."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Self, final

import typer

from punt_vox.client_errors import VoxdConnectionError, VoxdProtocolError
from punt_vox.commands.voice_call_script import VoiceCallScript
from punt_vox.commands.voice_call_status import CallStatusView

if TYPE_CHECKING:
    from pathlib import Path

    from punt_vox.commands.voice_call_follow import CallFollower
    from punt_vox.daemon_control import DaemonControl

__all__ = ["CallStartup"]

_START_TIMEOUT_S = 60.0
_STATUS_TIMEOUT_S = 30.0


@final
class CallStartup:
    """Start dialing and hand control to the follower once voxd answers."""

    __slots__ = ("_control", "_follower")
    _control: DaemonControl
    _follower: CallFollower

    def __new__(cls, control: DaemonControl, follower: CallFollower) -> Self:
        """Bind the RPC client and the follower for this CLI invocation."""
        self = super().__new__(cls)
        self._control = control
        self._follower = follower
        return self

    def start(self, script: Path | None) -> None:
        """Dial with microphone input or recorded turns from *script*."""
        fields: dict[str, object] = {}
        if script is not None:
            fields["script"] = VoiceCallScript.read(script).wire()
        try:
            started = self._begin(fields)
        except KeyboardInterrupt:
            # voxd may already be dialing the call this request asked for.
            self._follower.finish(self._follower.hang_up())
            return
        self._follower.finish(self._follower.until_end(started))

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
                asyncio.run(self._control.request("status", timeout=_STATUS_TIMEOUT_S))
            )
        except (VoxdConnectionError, VoxdProtocolError):
            return
        if status.failed:
            self._follower.finish(status)
