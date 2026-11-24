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
    ExpectPong,
    HangUp,
    SendAgentResponse,
    SendAudio,
    SendInterruption,
    SendPing,
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
_INITIATION_TYPE = "conversation_initiation_client_data"
_AUDIO_FORMAT = "pcm_16000"


@final
class FakeConvAIAgent:
    """Serve one scripted ElevenLabs conversation on a loopback port.

    The agent records the client's initiation payload, keeps every client
    message in ``received``, and plays its steps in order. A client close ends
    the script without raising. Any other error in the reader or the player is
    the agent's ``fault``: the socket closes with code 1011 so the client sees
    it at once, and leaving the context re-raises it so the test fails with
    the real error rather than a timeout.
    """

    __slots__ = (
        "_audio_chunks",
        "_changed",
        "_closed",
        "_connections",
        "_cut_short",
        "_fault",
        "_initiation",
        "_pongs",
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
    _pongs: set[int]
    _audio_chunks: int
    _closed: bool
    _fault: BaseException | None  # None until the reader or player fails.
    _cut_short: Step | None  # None unless a client close ended a wait step.
    _connections: int
    _changed: asyncio.Condition

    def __new__(cls, steps: Iterable[Step]) -> Self:
        self = super().__new__(cls)
        self._steps = tuple(steps)
        self._stack = AsyncExitStack()
        self._port = 0
        self._initiation = {}
        self._received = []
        self._tool_results = {}
        self._pongs = set()
        self._audio_chunks = 0
        self._closed = False
        self._fault = None
        self._cut_short = None
        self._connections = 0
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

    @property
    def pongs(self) -> frozenset[int]:
        return frozenset(self._pongs)

    @property
    def fault(self) -> BaseException | None:  # None while the fake has not failed.
        return self._fault

    @property
    def cut_short(self) -> Step | None:  # None when every wait step was satisfied.
        return self._cut_short

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
        if self._fault is not None:
            raise self._fault

    async def _handle(self, socket: ServerConnection) -> None:
        self._connections += 1
        if self._connections > 1:
            # One script, one conversation: a second dial is a daemon bug.
            msg = f"second connection to the fake agent (#{self._connections})"
            await self._fail(socket, RuntimeError(msg))
            return
        tasks = {
            asyncio.create_task(self._read(socket)),
            asyncio.create_task(self._play(socket)),
        }
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
            faults = [exc for task in done if (exc := task.exception()) is not None]
            if faults:
                await self._fail(socket, faults[0])
        finally:
            for task in tasks:
                await self._stop(task)

    async def _play(self, socket: ServerConnection) -> None:
        # The client hanging up is the normal end of a script, not a fault.
        with suppress(ConnectionClosed):
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
            case SendPing(event_id):
                body = {"event_id": event_id, "ping_ms": 0}
                await socket.send(json.dumps({"type": "ping", "ping_event": body}))
            case ExpectPong(event_id):
                await self._wait_for(step, lambda: event_id in self._pongs)
            case WaitForToolResult(tool_call_id):
                await self._wait_for(step, lambda: tool_call_id in self._tool_results)
            case ExpectAudioChunks(count):
                await self._wait_for(step, lambda: self._audio_chunks >= count)
            case HangUp():
                await socket.close()
                return True
        return False

    async def _read(self, socket: ServerConnection) -> None:
        try:
            with suppress(ConnectionClosed):
                async for raw in socket:
                    await self._record(json.loads(raw))
        finally:
            await self._mark_closed()

    async def _fail(self, socket: ServerConnection, exc: BaseException) -> None:
        """Keep the first error and close the socket so the client sees it now."""
        if self._fault is None:
            self._fault = exc
        await socket.close(code=1011, reason=f"fake agent fault: {exc}"[:123])

    async def _record(self, message: Message) -> None:
        if not self._initiation:
            kind = message.get("type")
            if kind != _INITIATION_TYPE:
                msg = f"first client message must be {_INITIATION_TYPE}, not {kind!r}"
                raise ValueError(msg)
            self._initiation = message
        else:
            self._received.append(message)
        if message.get("type") == "client_tool_result":
            self._tool_results[str(message["tool_call_id"])] = message
        elif message.get("type") == "pong":
            self._pongs.add(int(str(message["event_id"])))
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

    async def _wait_for(self, step: Step, satisfied: Callable[[], bool]) -> None:
        """Wait for a step's condition; a client close first is a cut-short script."""
        await self._wait_until(lambda: satisfied() or self._closed)
        if not satisfied() and self._cut_short is None:
            self._cut_short = step

    @staticmethod
    async def _stop(task: asyncio.Task[None]) -> None:
        """End a task without re-raising what it already failed with."""
        task.cancel()
        await asyncio.wait({task})

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
