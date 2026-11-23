"""Dial voxd's Mode B call and report failures before live status polling."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Self, final

import typer

from punt_vox.client_errors import VoxdConnectionError, VoxdProtocolError
from punt_vox.commands.voice_call_status import CallStatusView

if TYPE_CHECKING:
    from punt_vox.commands.voice_call_follow import CallFollower
    from punt_vox.daemon_control import DaemonControl

__all__ = ["CallStartup"]

_START_TIMEOUT_S = 60.0


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

    def start(self, fields: dict[str, object]) -> None:
        """Dial with the caller's input and echo protection options."""
        try:
            started = self._begin(fields)
        except KeyboardInterrupt:
            # voxd may already be dialing the call this request asked for.
            self._follower.finish(self._follower.hang_up())
            return
        if started.failed:
            typer.echo("Call unavailable", err=True)
            self._follower.finish(started)
        else:
            self._follower.finish(self._follower.until_end(started))

    def _begin(self, fields: dict[str, object]) -> CallStatusView:
        """Send ``call_start``; a call that failed to connect reports its trace."""
        try:
            return CallStatusView(
                asyncio.run(
                    self._control.request("start", timeout=_START_TIMEOUT_S, **fields)
                )
            )
        except (VoxdConnectionError, VoxdProtocolError) as exc:
            typer.echo(f"Call unavailable: {exc}", err=True)
            raise typer.Exit(1) from None
