"""Typed client for the voice call lifecycle RPCs."""

from __future__ import annotations

from typing import ClassVar, Literal, Self, final

from punt_vox.client_errors import VoxdProtocolError
from punt_vox.commands.voice_call_status import CallStatusView
from punt_vox.daemon_control import DaemonControl

__all__ = ["CallControl"]

type _CallOperation = Literal["start_scoped", "status", "stop_scoped"]


@final
class CallControl:
    """Frame call requests and validate the status replies.

    Distinct scoped start and stop routes make an older daemon refuse the
    operation instead of silently ignoring the call ID.
    """

    __slots__ = ("_rpc",)
    _rpc: DaemonControl
    _TIMEOUTS: ClassVar[dict[_CallOperation, float]] = {
        "start_scoped": 60.0,
        "status": 30.0,
        "stop_scoped": 30.0,
    }

    def __new__(cls) -> Self:
        """Bind the call RPC family."""
        self = super().__new__(cls)
        self._rpc = DaemonControl("call")
        return self

    async def request(
        self, operation: _CallOperation, **fields: object
    ) -> CallStatusView:
        """Send a call operation within its time budget and validate the reply."""
        try:
            result = await self._rpc.request(
                operation, timeout=self._TIMEOUTS[operation], **fields
            )
        except VoxdProtocolError as exc:
            if str(exc) != f"unknown message type: call_{operation}":
                raise
            raise VoxdProtocolError(
                "voxd does not support this call operation; upgrade or restart voxd"
            ) from exc
        view = CallStatusView(result)
        if view.state != "idle" and not view.call_id:
            raise VoxdProtocolError(
                "Missing call ID in voxd status; upgrade or restart voxd"
            )
        return view

    async def status_snapshot(self) -> object:
        """Return the daemon's status payload for observation, not call control."""
        return await self._rpc.request("status", timeout=self._TIMEOUTS["status"])

    async def teardown(self) -> None:
        """Remove the managed agent without interpreting the unused call status."""
        # Seven serial provider deletes at 10 seconds each, plus locking and I/O.
        await self._rpc.request("teardown", timeout=90.0)
