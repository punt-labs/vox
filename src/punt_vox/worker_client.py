"""Thin worker control RPC client using voxd's authenticated transport."""

from __future__ import annotations

from typing import final
from uuid import uuid4

from punt_vox.client_errors import VoxdProtocolError
from punt_vox.client_transport import VoxdTransport


@final
class WorkerClient:
    """Make short-lived worker requests without local lifecycle logic."""

    __slots__ = ()

    @staticmethod
    async def request(operation: str, **fields: str) -> object:
        """Return a registry result or surface the daemon's refusal."""
        transport = VoxdTransport(None, None, None)
        request_id = uuid4().hex
        await transport.connect()
        try:
            reply = await transport.send_and_recv(
                {"type": f"worker_{operation}", "id": request_id, **fields},
                timeout=90 if operation == "start" else 30,
            )
        finally:
            await transport.close()
        if reply.get("type") != "worker_result" or reply.get("id") != request_id:
            raise VoxdProtocolError(
                str(reply.get("message", "Unexpected worker reply"))
            )
        return reply.get("result")
