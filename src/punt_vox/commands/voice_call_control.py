"""Typed client for the voice call lifecycle RPCs."""

from __future__ import annotations

from typing import ClassVar, Literal, Self, final

from punt_vox.commands.voice_call_status import CallStatusView
from punt_vox.daemon_control import DaemonControl

__all__ = ["CallControl"]

type _CallOperation = Literal["start", "status", "stop", "teardown"]


@final
class CallControl:
    """Frame call requests and validate the status replies."""

    __slots__ = ("_rpc",)
    _rpc: DaemonControl
    _TIMEOUTS: ClassVar[dict[_CallOperation, float]] = {
        "start": 60.0,
        "status": 30.0,
        "stop": 30.0,
        # Seven serial provider deletes at 10 seconds each, plus locking and I/O.
        "teardown": 90.0,
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
        return CallStatusView(
            await self._rpc.request(
                operation, timeout=self._TIMEOUTS[operation], **fields
            )
        )
