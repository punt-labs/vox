"""Drive one initialized ElevenLabs conversation and pair tool results."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Self, final

from punt_vox.voxd.voice_agent._decode import EventDecoder, Message
from punt_vox.voxd.voice_agent._socket import Socket
from punt_vox.voxd.voice_agent.events import (
    AgentToolResponse,
    ClientToolCall,
    Metadata,
    Ping,
)
from punt_vox.voxd.voice_agent.fault import ConvAIError
from punt_vox.voxd.voice_agent.sender import ConversationSender

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from websockets.asyncio.client import ClientConnection

    from punt_vox.voxd.voice_agent.contracts import (
        OptionalTrace,
        SessionOptions,
        SessionStart,
    )
    from punt_vox.voxd.voice_agent.events import Decoded, Event

__all__ = ["Conversation"]

_METADATA_TIMEOUT_S = 15


@final
class Conversation:
    """Operate one session on one event loop, with one active event reader.

    Keep receiving while running a slow tool so protocol pings can be answered.
    The caller owns tool dispatch and reports its result against the given ID.
    """

    __slots__ = ("_metadata", "_sender", "_socket", "_trace")
    _socket: ClientConnection
    _metadata: Metadata
    _trace: OptionalTrace
    _sender: ConversationSender

    def __new__(
        cls, socket: ClientConnection, metadata: Metadata, trace: OptionalTrace
    ) -> Self:
        """Wrap an initialized WebSocket connection."""
        self = super().__new__(cls)
        self._socket = socket
        self._metadata = metadata
        self._trace = trace
        self._sender = ConversationSender(socket, trace)
        return self

    @classmethod
    @asynccontextmanager
    async def open(
        cls,
        signed_url: str,
        options: SessionOptions,
        *,
        trace: OptionalTrace = None,
        allow_loopback: bool = False,
    ) -> AsyncGenerator[Self]:
        """Send the session override and wait for metadata before yielding."""
        socket = await Socket.open(signed_url, allow_loopback=allow_loopback)
        try:
            if trace is not None:
                trace.record("note", "ws_open", {"signed_url": signed_url})
            metadata = await cls._initialize(socket, options, trace)
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
                case ClientToolCall() if not self._sender.admit(event):
                    continue
                case ClientToolCall():
                    return event
                case _:
                    return event

    async def send_text(self, text: str) -> None:
        """Send one user message to the active conversation."""
        await self._sender.send_text(text)

    async def send_context(self, text: str) -> None:
        """Send hidden background information without requesting a turn."""
        await self._sender.send_context(text)

    async def send_audio(self, pcm: bytes) -> None:
        """Send one chunk in the negotiated input audio format."""
        await self._sender.send_audio(pcm)

    async def send_tool_result(
        self, tool_call_id: str, result: str, *, is_error: bool = False
    ) -> None:
        """Answer one pending client tool call at most once."""
        await self._sender.send_tool_result(tool_call_id, result, is_error=is_error)

    @staticmethod
    async def _start(
        socket: ClientConnection, options: SessionOptions, trace: OptionalTrace
    ) -> None:
        payload: SessionStart = {
            "type": "conversation_initiation_client_data",
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
        }
        await Socket.send(socket, payload)
        if trace is not None:
            trace.record(
                "send",
                payload["type"],
                {"text_only": options.text_only, "tools": len(options.tool_ids)},
            )

    @classmethod
    async def _initialize(
        cls, socket: ClientConnection, options: SessionOptions, trace: OptionalTrace
    ) -> Metadata:
        try:
            await cls._start(socket, options, trace)
            async with asyncio.timeout(_METADATA_TIMEOUT_S):
                return await cls._read_metadata(socket, trace)
        except TimeoutError:
            msg = "Timed out waiting for ElevenLabs conversation metadata"
            raise ConvAIError(msg) from None
        except EOFError:
            msg = "ElevenLabs closed the conversation before initialization"
            raise ConvAIError(msg) from None

    @classmethod
    async def _read_metadata(
        cls, socket: ClientConnection, trace: OptionalTrace
    ) -> Metadata:
        event = await cls._read(socket, trace)
        if not isinstance(event, Metadata):
            msg = "ElevenLabs sent conversation data before initialization metadata"
            raise ConvAIError(msg)
        return event

    @classmethod
    async def _read(
        cls, socket: ClientConnection, trace: OptionalTrace
    ) -> Event | Metadata:
        while True:
            message = Message.from_frame(await Socket.receive(socket))
            kind = message.text("type")
            event = EventDecoder.decode(kind, message)
            if event is None:
                if trace is not None:
                    trace.record("recv", "ignored_event", {})
                continue
            if trace is not None:
                trace.record("recv", kind, cls._trace_fields(event))
            if not isinstance(event, Ping):
                return event
            await Socket.send(socket, {"type": "pong", "event_id": event.event_id})
            if trace is not None:
                trace.record("send", "pong", {"event_id": event.event_id})

    @staticmethod
    def _trace_fields(
        event: Decoded,
    ) -> dict[str, object]:  # Each event has different fields.
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
