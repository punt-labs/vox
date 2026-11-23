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
from punt_vox.commands.voice_call_startup import CallStartup
from punt_vox.commands.voice_call_status import CallStatusView
from punt_vox.daemon_control import DaemonControl

if TYPE_CHECKING:
    from collections.abc import Coroutine
    from pathlib import Path

__all__ = ["VoiceCall"]

_CONTROL_TIMEOUT_S = 30.0
# Seven serial provider deletes at 10 seconds each, plus locking and I/O.
_TEARDOWN_TIMEOUT_S = 90.0
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
        follower = CallFollower(self._control, self._poll_s)
        CallStartup(self._control, follower).start(script)

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

    def teardown(self) -> None:
        """Remove only the managed agent and tools, not calls or workers."""
        self._run(self._control.request("teardown", timeout=_TEARDOWN_TIMEOUT_S))
        typer.echo("The next vox call will provision a new agent.")

    @staticmethod
    def _run(request: Coroutine[object, object, object]) -> object:
        """Run one coroutine, turning an unreachable or refusing daemon into exit 1."""
        try:
            return asyncio.run(request)
        except (VoxdConnectionError, VoxdProtocolError) as exc:
            typer.echo(f"Call unavailable: {exc}", err=True)
            raise typer.Exit(1) from None
