"""One short-lived control request to voxd, for a family of lifecycle RPCs."""

from __future__ import annotations

from typing import Self, final
from uuid import uuid4

from punt_vox.client_errors import VoxdProtocolError
from punt_vox.client_transport import VoxdTransport

__all__ = ["DaemonControl"]


@final
class DaemonControl:
    """Send ``<family>_<operation>`` and return the ``<family>_result`` payload.

    The daemon owns the lifecycle and the policy; this client only frames the
    request, checks the reply is the answer to it, and surfaces refusals.
    """

    __slots__ = ("_family",)
    _family: str

    def __new__(cls, family: str) -> Self:
        """Bind the RPC family, such as ``worker`` or ``call``."""
        self = super().__new__(cls)
        self._family = family
        return self

    async def request(
        self, operation: str, *, timeout: float, **fields: object
    ) -> object:
        """Return the daemon's result or raise its refusal.

        Raises:
            VoxdConnectionError: If the daemon is unreachable or the connection is lost.
            VoxdProtocolError: If the daemon refuses, or answers something else.
        """
        transport = VoxdTransport(None, None, None)
        request_id = uuid4().hex
        await transport.connect()
        try:
            reply = await transport.send_and_recv(
                {"type": f"{self._family}_{operation}", "id": request_id, **fields},
                timeout=timeout,
            )
        finally:
            await transport.close()
        if (
            reply.get("type") != f"{self._family}_result"
            or reply.get("id") != request_id
        ):
            raise VoxdProtocolError(
                str(reply.get("message", f"Unexpected {self._family} reply"))
            )
        return reply.get("result")
