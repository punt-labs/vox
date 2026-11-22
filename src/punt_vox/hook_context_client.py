"""Hook context RPCs over the regular authenticated voxd transport."""

from __future__ import annotations

from typing import cast
from uuid import uuid4

from punt_vox.client_errors import VoxdProtocolError
from punt_vox.client_transport import VoxdTransport


class HookContextClient:
    """Short-lived RPCs shared by the relay and `vox call context`."""

    @classmethod
    async def send_event(cls, payload: dict[str, object]) -> None:
        """Deliver one stamped hook payload and check the daemon's receipt."""
        await cls._request(
            {"type": "hook_event", "payload": payload}, expected="hook_event_accepted"
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
