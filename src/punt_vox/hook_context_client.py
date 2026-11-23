"""Hook context RPCs over the regular authenticated voxd transport."""

from __future__ import annotations

import asyncio
import os
from typing import ClassVar, cast, final
from uuid import uuid4

from punt_vox.client_errors import (
    VoxdConnectionError,
    VoxdHandshakeRejectedError,
    VoxdProtocolError,
    VoxdRecoveryPendingError,
)
from punt_vox.client_transport import VoxdTransport


@final
class HookContextClient:
    """Short-lived RPCs shared by the relay and `vox call context`."""

    __slots__ = ()
    _RETRY_DELAYS: ClassVar[tuple[float, ...]] = (0.1, 0.2, 0.4, 0.8, 1.0, 1.0, 1.0)

    @classmethod
    async def send_event(
        cls, payload: dict[str, object], *, worker_only: bool = False
    ) -> None:
        """Deliver one stamped hook payload and check the daemon's receipt."""
        managed_id = os.environ.get("VOX_WORKER_SESSION_ID")
        managed = worker_only or (
            managed_id is not None and managed_id == payload.get("session_id")
        )
        if not managed:
            await cls._deliver(payload, worker_only=False)
            return
        async with asyncio.timeout(5):
            for delay in (*cls._RETRY_DELAYS, None):
                try:
                    await cls._deliver(payload, worker_only=worker_only)
                except (VoxdConnectionError, VoxdProtocolError) as exc:
                    if delay is None or not cls._retryable(exc):
                        raise
                    await asyncio.sleep(delay)
                else:
                    return

    @staticmethod
    def _retryable(exc: VoxdConnectionError | VoxdProtocolError) -> bool:
        if isinstance(exc, VoxdProtocolError):
            return isinstance(exc, VoxdRecoveryPendingError) or isinstance(
                exc.__cause__, TimeoutError
            )
        return not isinstance(exc, VoxdHandshakeRejectedError)

    @classmethod
    async def _deliver(cls, payload: dict[str, object], *, worker_only: bool) -> None:
        """Start each attempt at admission, preserving the stamped event."""
        if worker_only:
            admission = await cls._request(
                {
                    "type": "worker_hook_admission",
                    "session_id": payload.get("session_id"),
                    "cwd": payload.get("cwd"),
                },
                expected="worker_hook_admission",
            )
            if admission.get("admitted") is not True:
                return
        await cls._request(
            {"type": "hook_event", "payload": payload, "worker_only": worker_only},
            expected="hook_event_accepted",
        )

    @classmethod
    async def snapshot(cls, session: str) -> dict[str, object]:
        """Fetch the curated context for an explicit hook session ID."""
        reply = await cls._request(
            {"type": "hook_context", "session_id": session}, expected="hook_context"
        )
        snapshot = reply.get("snapshot")
        if not isinstance(snapshot, dict):
            msg = "voxd hook context reply has no snapshot object"
            raise VoxdProtocolError(msg)
        return cast("dict[str, object]", snapshot)

    @staticmethod
    async def _request(
        request: dict[str, object], *, expected: str
    ) -> dict[str, object]:
        transport = VoxdTransport(None, None, None)
        request_id = uuid4().hex
        await transport.connect()
        try:
            reply = await transport.send_and_recv(
                {**request, "id": request_id}, timeout=3
            )
        finally:
            await transport.close()
        if reply.get("type") != expected or reply.get("id") != request_id:
            msg = "unexpected voxd hook context reply"
            raise VoxdProtocolError(msg)
        return reply
