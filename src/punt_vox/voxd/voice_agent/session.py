"""Drive one initialized ElevenLabs conversation and pair tool results."""

from __future__ import annotations

import asyncio
import base64
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Self, final

from punt_vox.voxd.voice_agent._decode import EventDecoder, Message, Ping
from punt_vox.voxd.voice_agent._socket import Socket
from punt_vox.voxd.voice_agent.events import AgentToolResponse, ClientToolCall, Metadata
from punt_vox.voxd.voice_agent.fault import ConvAIError

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from websockets.asyncio.client import ClientConnection

    from punt_vox.voxd.voice_agent._decode import Decoded
    from punt_vox.voxd.voice_agent.events import Event
    from punt_vox.voxd.voice_agent.trace import EventTrace

__all__ = ["Conversation", "SessionOptions"]


@dataclass(frozen=True, slots=True)
class SessionOptions:
    """Select the prompt and the registered client-tool schemas for a call."""

    prompt: str
    tool_ids: tuple[str, ...]
    text_only: bool = False


@final
class Conversation:
    """Operate one session on one event loop, with one active event reader.

    Keep receiving while running a slow tool so protocol pings can be answered.
    The caller owns tool dispatch and reports its result against the given ID.
    """

    __slots__ = ("_metadata", "_socket", "_tool_calls", "_trace")
    _socket: ClientConnection
    _metadata: Metadata
    _trace: EventTrace | None
    _tool_calls: dict[str, bool]

    def __new__(
        cls, socket: ClientConnection, metadata: Metadata, trace: EventTrace | None
    ) -> Self:
        """Wrap an initialized WebSocket connection."""
        self = super().__new__(cls)
        self._socket = socket
        self._metadata = metadata
        self._trace = trace
        self._tool_calls = {}
        return self

    @classmethod
    @asynccontextmanager
    async def open(
        cls,
        signed_url: str,
        options: SessionOptions,
        *,
        trace: EventTrace | None = None,
    ) -> AsyncGenerator[Self]:
        """Send the session override and wait for metadata before yielding."""
        socket = await Socket.open(signed_url)
        try:
            if trace is not None:
                trace.record("note", "ws_open", {"signed_url": signed_url})
            await cls._start(socket, options, trace)
            async with asyncio.timeout(15):
                metadata = await cls._read_metadata(socket, trace)
            yield cls(socket, metadata, trace)
        finally:
            await socket.close()

    @property
    def metadata(self) -> Metadata:
        """Return the negotiated conversation identity and audio formats."""
        return self._metadata

    async def receive(self) -> Event:
        """Deliver the next event, handling pings and replayed calls in place."""
        while True:
            event = await self._read(self._socket, self._trace)
            match event:
                case Metadata():
                    msg = "ElevenLabs sent duplicate conversation metadata"
                    raise ConvAIError(msg)
                case ClientToolCall() if event.tool_call_id in self._tool_calls:
                    continue
                case ClientToolCall():
                    self._tool_calls[event.tool_call_id] = event.expects_response
                    return event
                case _:
                    return event

    async def send_text(self, text: str) -> None:
        """Send one user message to the active conversation."""
        await self._send({"type": "user_message", "text": text}, "user_message")

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
        payload: dict[str, object],
        kind: str,
        detail: dict[str, object] | None = None,
    ) -> None:
        await Socket.send(self._socket, payload)
        if self._trace is not None:
            self._trace.record("send", kind, {} if detail is None else detail)

    @staticmethod
    async def _start(
        socket: ClientConnection, options: SessionOptions, trace: EventTrace | None
    ) -> None:
        kind = "conversation_initiation_client_data"
        await Socket.send(
            socket,
            {
                "type": kind,
                "conversation_config_override": {
                    "agent": {
                        "prompt": {
                            "prompt": options.prompt,
                            "tool_ids": list(options.tool_ids),
                            "tool_ids_strict": True,
                        },
                    },
                    "conversation": {"text_only": options.text_only},
                },
            },
        )
        if trace is not None:
            trace.record(
                "send",
                kind,
                {"text_only": options.text_only, "tools": len(options.tool_ids)},
            )

    @classmethod
    async def _read_metadata(
        cls, socket: ClientConnection, trace: EventTrace | None
    ) -> Metadata:
        event = await cls._read(socket, trace)
        if not isinstance(event, Metadata):
            msg = "ElevenLabs sent conversation data before initialization metadata"
            raise ConvAIError(msg)
        return event

    @classmethod
    async def _read(
        cls, socket: ClientConnection, trace: EventTrace | None
    ) -> Event | Metadata:
        while True:
            message = Message.from_frame(await Socket.receive(socket))
            kind = message.text("type")
            event = EventDecoder.decode(kind, message)
            if event is None:
                continue
            if trace is not None:
                trace.record("recv", kind, cls._trace_fields(event))
            if not isinstance(event, Ping):
                return event
            pong: dict[str, object] = {"type": "pong", "event_id": event.event_id}
            await Socket.send(socket, pong)
            if trace is not None:
                trace.record("send", "pong", {"event_id": event.event_id})

    @staticmethod
    def _trace_fields(event: Decoded) -> dict[str, object]:
        match event:
            case ClientToolCall():
                return {
                    "tool_call_id": event.tool_call_id,
                    "tool_name": event.tool_name,
                }
            case AgentToolResponse():
                return {"tool_call_id": event.tool_call_id, "is_error": event.is_error}
            case Metadata():
                return {"conversation_id": event.conversation_id}
            case _:
                return {"event_id": event.event_id}
