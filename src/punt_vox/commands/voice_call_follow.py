"""Follow voxd's running voice call on one terminal line until it ends."""

from __future__ import annotations

import asyncio
import shutil
import sys
from typing import TYPE_CHECKING, Self, final

import typer

from punt_vox.client_errors import VoxdConnectionError, VoxdProtocolError

if TYPE_CHECKING:
    from punt_vox.commands.voice_call_control import CallControl
    from punt_vox.commands.voice_call_status import CallStatusView

__all__ = ["CallFollower"]


@final
class CallFollower:
    """Show a started call's live status line; hang it up when left unwatched."""

    __slots__ = ("_control", "_poll_s")
    _control: CallControl
    _poll_s: float

    def __new__(cls, control: CallControl, poll_s: float) -> Self:
        """Follow through the ``call_*`` *control*, polling every *poll_s* seconds."""
        self = super().__new__(cls)
        self._control = control
        self._poll_s = poll_s
        return self

    def until_end(self, started: CallStatusView) -> CallStatusView:
        """Follow *started* to its end and return its last status.

        Ctrl-C hangs the call up. Losing voxd's status hangs it up too, rather
        than leave it running with nobody watching it.

        Raises:
            typer.Exit: Exit 1 when the status is lost or the hangup does not
                take; 130 when a second Ctrl-C interrupts the hangup.
        """
        try:
            return asyncio.run(self._follow(started))
        except KeyboardInterrupt:
            return self.hang_up(started.call_id)
        except (VoxdConnectionError, VoxdProtocolError) as exc:
            typer.echo(f"Lost the call's status, hanging up: {exc}", err=True)
            self.finish(self.hang_up(started.call_id))
            raise typer.Exit(1) from None

    @staticmethod
    def finish(view: CallStatusView) -> None:
        """Print the end-of-call report; a failed call exits 1.

        Raises:
            typer.Exit: Exit 1 when the call failed.
        """
        if sys.stdout.isatty():
            typer.echo("")
        for line in view.report():
            typer.echo(line)
        if view.failed:
            raise typer.Exit(1)

    async def _follow(self, view: CallStatusView) -> CallStatusView:
        """Poll ``call_status``, echoing the status line each time it changes.

        Following stops at this call's end, or when voxd reports a different
        call (another client started one after this one ended).
        """
        self._show(view.line())
        while view.running:
            await asyncio.sleep(self._poll_s)
            polled = await self._control.request("status")
            if polled.call_id != view.call_id:
                break
            if polled.line() != view.line():
                self._show(polled.line())
            view = polled
        return view

    def hang_up(self, call_id: str) -> CallStatusView:
        """Stop the named call and return its ended status."""
        try:
            ended = asyncio.run(self._control.request("stop_scoped", call_id=call_id))
        except KeyboardInterrupt:
            typer.echo(
                "\nInterrupted while hanging up; the call may still be live:"
                " run `vox call status`.",
                err=True,
            )
            raise typer.Exit(130) from None
        except (VoxdConnectionError, VoxdProtocolError) as exc:
            typer.echo(f"Could not hang up: {exc}; run `vox call status`.", err=True)
            raise typer.Exit(1) from None
        if ended.running:
            typer.echo(
                f"voxd still reports the call {ended.state}; run `vox call status`.",
                err=True,
            )
            raise typer.Exit(1)
        return ended

    @staticmethod
    def _show(line: str) -> None:
        """Rewrite the status line in place on a terminal, else print it.

        On a terminal the line is clipped to its width: a wrapped line would
        leave its first rows behind when the next one rewrites only the last.
        """
        if sys.stdout.isatty():
            width = shutil.get_terminal_size().columns - 1
            if len(line) > width:
                line = line[: max(width - 3, 0)] + "..."
            typer.echo(f"\r\x1b[K{line}", nl=False)
        else:
            typer.echo(line)
