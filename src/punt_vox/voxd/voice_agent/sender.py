"""Outbound conversation messages and client tool response pairing."""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING, Self, final

from punt_vox.voxd.voice_agent._socket import Socket

if TYPE_CHECKING:
    from websockets.asyncio.client import ClientConnection

    from punt_vox.voxd.voice_agent.contracts import ClientMessage, OptionalTrace
    from punt_vox.voxd.voice_agent.events import ClientToolCall


@final
class ConversationSender:
    """Own outgoing framing, tracing and exactly-once tool replies."""

    __slots__ = ("_socket", "_tool_calls", "_trace")
    _socket: ClientConnection
    _trace: OptionalTrace
    _tool_calls: dict[str, bool]

    def __new__(cls, socket: ClientConnection, trace: OptionalTrace) -> Self:
        """Bind a socket and its optional trace."""
        self = super().__new__(cls)
        self._socket, self._trace = socket, trace
        self._tool_calls = {}
        return self

    def admit(self, call: ClientToolCall) -> bool:
        """Remember new calls and reject duplicates, even after a reply."""
        if call.tool_call_id in self._tool_calls:
            return False
        self._tool_calls[call.tool_call_id] = call.expects_response
        return True

    async def send_text(self, text: str) -> None:
        """Send one user message to the active conversation."""
        await self._send({"type": "user_message", "text": text}, "user_message")

    async def send_context(self, text: str) -> None:
        """Add hidden context without requesting an agent turn."""
        await self._send(
            {"type": "contextual_update", "text": text},
            "contextual_update",
        )

    async def send_audio(self, pcm: bytes) -> None:
        """Send one chunk in the negotiated input audio format."""
        await self._send(
            {"user_audio_chunk": base64.b64encode(pcm).decode("ascii")},
            "user_audio_chunk",
        )

    async def send_tool_result(
        self, tool_call_id: str, result: str, *, is_error: bool = False
    ) -> None:
        """Send a single result for a pending client tool call ID."""
        if not self._tool_calls.get(tool_call_id):
            msg = "No pending ElevenLabs client tool call expects that result"
            raise ValueError(msg)
        self._tool_calls[tool_call_id] = False
        await self._send(
            {
                "type": "client_tool_result",
                "tool_call_id": tool_call_id,
                "result": result,
                "is_error": is_error,
            },
            "client_tool_result",
            {"tool_call_id": tool_call_id, "is_error": is_error},
        )

    async def _send(
        self,
        payload: ClientMessage,
        kind: str,
        detail: dict[str, object] | None = None,  # Trace fields vary by event.
    ) -> None:
        await Socket.send(self._socket, payload)
        if self._trace is not None:
            self._trace.record("send", kind, {} if detail is None else detail)
