"""A scripted fake of the ElevenLabs Conversational AI websocket endpoint."""

from __future__ import annotations

import asyncio
import base64
import json
from contextlib import AsyncExitStack, suppress
from typing import TYPE_CHECKING, Self, final

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from .steps import (
    ExpectAudioChunks,
    HangUp,
    SendAgentResponse,
    SendAudio,
    SendInterruption,
    SendToolCall,
    SendUserTranscript,
    WaitForToolResult,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from types import TracebackType

    from websockets.asyncio.server import ServerConnection

    from .steps import Step

__all__ = ["FakeConvAIAgent"]

# Every message is a JSON object at the wire boundary; tests narrow the values.
type Message = dict[str, object]

_CONVERSATION_ID = "conv_fake"
_AUDIO_FORMAT = "pcm_16000"


@final
class FakeConvAIAgent:
    """Serve one scripted ElevenLabs conversation on a loopback port.

    The agent records the client's initiation payload, answers pings, keeps
    every client message in ``received``, and plays its steps in order. A
    client close ends the script without raising.
    """

    __slots__ = (
        "_audio_chunks",
        "_changed",
        "_closed",
        "_initiation",
        "_port",
        "_received",
        "_stack",
        "_steps",
        "_tool_results",
    )
    _steps: tuple[Step, ...]
    _stack: AsyncExitStack
    _port: int  # Zero until the server binds in __aenter__.
    _initiation: Message  # Empty until the client's first message arrives.
    _received: list[Message]
    _tool_results: dict[str, Message]
    _audio_chunks: int
    _closed: bool
    _changed: asyncio.Condition

    def __new__(cls, steps: Iterable[Step]) -> Self:
        self = super().__new__(cls)
        self._steps = tuple(steps)
        self._stack = AsyncExitStack()
        self._port = 0
        self._initiation = {}
        self._received = []
        self._tool_results = {}
        self._audio_chunks = 0
        self._closed = False
        self._changed = asyncio.Condition()
        return self

    @property
    def signed_url(self) -> str:
        return f"ws://127.0.0.1:{self._port}/v1/convai/conversation?agent_id=fake"

    @property
    def initiation(self) -> Message:
        return self._initiation

    @property
    def received(self) -> list[Message]:
        return self._received

    @property
    def tool_results(self) -> dict[str, Message]:
        return self._tool_results

    @property
    def audio_chunks(self) -> int:
        return self._audio_chunks

    async def __aenter__(self) -> Self:
        server = await self._stack.enter_async_context(
            serve(self._handle, "127.0.0.1", 0)
        )
        self._port = server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,  # Context-manager protocol shape.
        exc: BaseException | None,  # Context-manager protocol shape.
        tb: TracebackType | None,  # Context-manager protocol shape.
    ) -> None:
        await self._stack.aclose()

    async def _handle(self, socket: ServerConnection) -> None:
        reader = asyncio.create_task(self._read(socket))
        try:
            # The client hanging up is the normal end of a script, not a fault.
            with suppress(ConnectionClosed):
                await self._play(socket)
        finally:
            await self._stop_reading(reader)

    async def _play(self, socket: ServerConnection) -> None:
        await self._wait_until(lambda: bool(self._initiation) or self._closed)
        if self._closed:
            return
        await socket.send(json.dumps(self._metadata()))
        for step in self._steps:
            if await self._run(step, socket):
                return
        await socket.wait_closed()

    async def _run(self, step: Step, socket: ServerConnection) -> bool:
        """Play one step; return True when the step ended the conversation."""
        match step:
            case SendAudio(pcm, event_id):
                body = {
                    "audio_base_64": base64.b64encode(pcm).decode(),
                    "event_id": event_id,
                }
                await socket.send(json.dumps({"type": "audio", "audio_event": body}))
            case SendUserTranscript(text):
                body = {"user_transcript": text}
                await socket.send(
                    json.dumps(
                        {"type": "user_transcript", "user_transcription_event": body}
                    )
                )
            case SendAgentResponse(text, event_id):
                body = {"agent_response": text, "event_id": event_id}
                await socket.send(
                    json.dumps({"type": "agent_response", "agent_response_event": body})
                )
            case SendToolCall(name, tool_call_id, parameters):
                call = {
                    "tool_call_id": tool_call_id,
                    "tool_name": name,
                    "parameters": parameters,
                    "expects_response": True,
                }
                await socket.send(
                    json.dumps({"type": "client_tool_call", "client_tool_call": call})
                )
            case SendInterruption(event_id):
                body = {"event_id": event_id}
                await socket.send(
                    json.dumps({"type": "interruption", "interruption_event": body})
                )
            case WaitForToolResult(tool_call_id):
                await self._wait_until(
                    lambda: tool_call_id in self._tool_results or self._closed
                )
            case ExpectAudioChunks(count):
                await self._wait_until(
                    lambda: self._audio_chunks >= count or self._closed
                )
            case HangUp():
                await socket.close()
                return True
        return False

    async def _read(self, socket: ServerConnection) -> None:
        try:
            with suppress(ConnectionClosed):
                async for raw in socket:
                    await self._record(socket, json.loads(raw))
        finally:
            await self._mark_closed()

    async def _record(self, socket: ServerConnection, message: Message) -> None:
        if not self._initiation:
            self._initiation = message
        else:
            self._received.append(message)
        if message.get("type") == "client_tool_result":
            self._tool_results[str(message["tool_call_id"])] = message
        elif message.get("type") == "ping":
            await socket.send(
                json.dumps({"type": "pong", "event_id": message["event_id"]})
            )
        elif "user_audio_chunk" in message:
            self._audio_chunks += 1
        async with self._changed:
            self._changed.notify_all()

    async def _mark_closed(self) -> None:
        async with self._changed:
            self._closed = True
            self._changed.notify_all()

    async def _wait_until(self, predicate: Callable[[], bool]) -> None:
        async with self._changed:
            await self._changed.wait_for(predicate)

    @staticmethod
    async def _stop_reading(reader: asyncio.Task[None]) -> None:
        reader.cancel()
        with suppress(asyncio.CancelledError):
            await reader

    @staticmethod
    def _metadata() -> Message:
        event = {
            "conversation_id": _CONVERSATION_ID,
            "agent_output_audio_format": _AUDIO_FORMAT,
            "user_input_audio_format": _AUDIO_FORMAT,
        }
        return {
            "type": "conversation_initiation_metadata",
            "conversation_initiation_metadata_event": event,
        }
