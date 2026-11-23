"""Dial voxd's voice call and report failures before live status polling."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Self, final
from uuid import uuid4

import typer

from punt_vox.client_errors import VoxdConnectionError, VoxdProtocolError

if TYPE_CHECKING:
    from punt_vox.commands.voice_call_control import CallControl
    from punt_vox.commands.voice_call_follow import CallFollower
    from punt_vox.commands.voice_call_status import CallStatusView

__all__ = ["CallStartup"]


@final
class CallStartup:
    """Start dialing and hand control to the follower once voxd answers."""

    __slots__ = ("_control", "_follower")
    _control: CallControl
    _follower: CallFollower

    def __new__(cls, control: CallControl, follower: CallFollower) -> Self:
        """Bind the RPC client and the follower for this CLI invocation."""
        self = super().__new__(cls)
        self._control = control
        self._follower = follower
        return self

    def start(self, fields: dict[str, object]) -> None:
        """Dial with the caller's input and echo protection options."""
        call_id = uuid4().hex
        try:
            started = self._begin({**fields, "call_id": call_id})
        except KeyboardInterrupt:
            # voxd may already be dialing the call this request asked for.
            self._follower.finish(self._follower.hang_up(call_id))
            return
        if started.failed:
            typer.echo("Call unavailable", err=True)
            self._follower.finish(started)
        else:
            self._follower.finish(self._follower.until_end(started))

    def _begin(self, fields: dict[str, object]) -> CallStatusView:
        """Send the start request and surface a refusal without polling."""
        try:
            return asyncio.run(self._control.request("start_scoped", **fields))
        except (VoxdConnectionError, VoxdProtocolError) as exc:
            typer.echo(f"Call unavailable: {exc}", err=True)
            raise typer.Exit(1) from None
