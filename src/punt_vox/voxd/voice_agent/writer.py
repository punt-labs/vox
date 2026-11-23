"""Write framed conversation messages and their sanitized trace details."""

from __future__ import annotations

from typing import TYPE_CHECKING, Self, final

from punt_vox.voxd.voice_agent._socket import Socket

if TYPE_CHECKING:
    from websockets.asyncio.client import ClientConnection

    from punt_vox.voxd.voice_agent.contracts import ClientMessage, OptionalTrace


@final
class ConversationWriter:
    """Bind socket writes and trace records to the same conversation."""

    __slots__ = ("_socket", "_trace")
    _socket: ClientConnection
    _trace: OptionalTrace

    def __new__(cls, socket: ClientConnection, trace: OptionalTrace) -> Self:
        """Bind the socket and its optional trace."""
        self = super().__new__(cls)
        self._socket, self._trace = socket, trace
        return self

    async def send(
        self,
        payload: ClientMessage,
        kind: str,
        detail: dict[str, object] | None = None,  # Trace fields vary by event.
    ) -> None:
        """Write one message, then trace only its caller-selected detail."""
        await Socket.send(self._socket, payload)
        if self._trace is not None:
            self._trace.record("send", kind, {} if detail is None else detail)
