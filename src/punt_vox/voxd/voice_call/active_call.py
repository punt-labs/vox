"""Identity and lifecycle of the daemon's current voice call."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Self, final

from punt_vox.voxd.voice_call.log_text import LogText

if TYPE_CHECKING:
    from pathlib import Path
    from uuid import UUID

    from punt_vox.voxd.voice_call.call import VoiceAgentCall

__all__ = ["ActiveCall"]

logger = logging.getLogger(__name__)


@final
class ActiveCall:
    """One call's identity, task and trace file."""

    __slots__ = ("_call", "_call_id", "_task", "_trace_path")
    _call: VoiceAgentCall
    _call_id: UUID
    _task: asyncio.Task[None]
    _trace_path: Path

    def __new__(
        cls,
        call_id: UUID,
        call: VoiceAgentCall,
        task: asyncio.Task[None],
        trace_path: Path,
    ) -> Self:
        """Bind resources that belong to the same call."""
        self = super().__new__(cls)
        self._call_id = call_id
        self._call = call
        self._task = task
        self._trace_path = trace_path
        return self

    @property
    def running(self) -> bool:
        """Return whether the call still owns its resources."""
        return not self._task.done()

    def status(self) -> dict[str, object]:
        """Return this call's status with its identity and trace."""
        return {
            **self._call.status(),
            "call_id": self._call_id.hex,
            "trace": str(self._trace_path),
        }

    async def wait_connected(self, timeout: float) -> None:
        """Wait for the connection, or for the task to end before connecting.

        A task that ends first has already recorded why; only a task still
        running at the deadline is marked as never having connected.
        """
        connected = asyncio.ensure_future(self._call.connected())
        try:
            await asyncio.wait(
                {connected, self._task}, timeout=timeout, return_when="FIRST_COMPLETED"
            )
        finally:
            connected.cancel()
        if not connected.done() and not self._task.done():
            self._call.mark_failed("the ElevenLabs conversation did not connect")
            await self.cancel()

    async def cancel(self, call_id: UUID | None = None) -> None:
        """Cancel this task, rejecting a stop intended for another call."""
        if call_id is not None and call_id != self._call_id:
            raise ValueError("the requested call is no longer current")
        self._task.cancel()
        (ending,) = await asyncio.gather(self._task, return_exceptions=True)
        if isinstance(ending, Exception):
            logger.warning("Call task ended with %s", LogText.failure(ending))
