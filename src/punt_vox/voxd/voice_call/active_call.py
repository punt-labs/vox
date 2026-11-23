"""Identity and lifecycle of the daemon's current voice call."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Self, final
from uuid import UUID

from punt_vox.voxd.voice_agent.trace import EventTrace

if TYPE_CHECKING:
    from pathlib import Path
    from typing import TextIO

    from punt_vox.voxd.voice_call.call import VoiceAgentCall

__all__ = ["ActiveCall", "CallTrace"]


@dataclass(frozen=True, slots=True)
class CallTrace:
    """Resources for a call trace, reserved by its unique call ID."""

    path: Path
    stream: TextIO
    events: EventTrace

    @classmethod
    def reserve(cls, directory: Path, call_id: UUID) -> Self:
        """Create a private trace and refuse an ID that was already used."""
        path = directory / f"call-{call_id.hex}.jsonl"
        try:
            stream = os.fdopen(
                os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w"
            )
        except FileExistsError:
            raise ValueError("call_id has already been used") from None
        return cls(path, stream, EventTrace(stream))


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
        """Wait for the connection, marking it failed before cancellation."""
        try:
            await asyncio.wait_for(self._call.connected(), timeout)
        except TimeoutError:
            self._call.mark_failed("the ElevenLabs conversation did not connect")
            await self.cancel()

    async def cancel(self, call_id: UUID | None = None) -> None:
        """Cancel this task, rejecting a stop intended for another call."""
        if call_id is not None and call_id != self._call_id:
            raise ValueError("the requested call is no longer current")
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)

    @staticmethod
    def requested_id(msg: dict[str, object]) -> UUID | None:
        """Read the optional call ID without treating malformed IDs as absent."""
        if "call_id" not in msg:
            return None
        value = msg["call_id"]
        if not isinstance(value, str):
            raise ValueError("call_id must be a UUID string")
        try:
            return UUID(value)
        except ValueError:
            raise ValueError("call_id must be a UUID string") from None
