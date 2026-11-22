"""One Mode B call: caller audio in, agent audio out, boss tools answered."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from typing import TYPE_CHECKING, Final, Literal, Self, final

from punt_vox.voxd.voice_agent.events import (
    AgentResponse,
    Audio,
    ClientToolCall,
    Interruption,
    UserTranscript,
)
from punt_vox.voxd.voice_agent.fault import ConvAIError
from punt_vox.voxd.voice_agent.session import Conversation

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import AbstractAsyncContextManager

    from punt_vox.voxd.voice_agent.contracts import SessionOptions
    from punt_vox.voxd.voice_agent.trace import EventTrace
    from punt_vox.voxd.voice_call.boss_tools import BossTools
    from punt_vox.voxd.voice_call.contracts import CallInput
    from punt_vox.voxd.voice_call.speaker import AgentSpeaker

    type Opener = Callable[..., AbstractAsyncContextManager[Conversation]]

__all__ = ["CallState", "VoiceAgentCall"]

logger = logging.getLogger(__name__)

type CallState = Literal["connecting", "live", "ended", "failed"]

_HISTORY: Final = 20
# Failures a call can meet in normal operation; anything else is logged as a bug.
_EXPECTED: Final = (ConvAIError, OSError, ValueError, RuntimeError)


@final
class VoiceAgentCall:
    """Run the conversation; workers it starts outlive it."""

    __slots__ = (
        "_audio",
        "_connected",
        "_conversation_id",
        "_error",
        "_opener",
        "_speaker",
        "_state",
        "_tool_calls",
        "_tools",
        "_trace",
        "_transcript",
    )
    _tools: BossTools
    _audio: CallInput
    _speaker: AgentSpeaker
    _trace: EventTrace
    _opener: Opener
    _state: CallState
    _conversation_id: str | None
    _error: str | None
    _connected: asyncio.Event
    _transcript: deque[dict[str, str]]
    _tool_calls: deque[dict[str, object]]

    def __new__(
        cls,
        tools: BossTools,
        audio: CallInput,
        speaker: AgentSpeaker,
        *,
        trace: EventTrace,
        opener: Opener = Conversation.open,
    ) -> Self:
        """Bind the call's tools, caller audio, speaker and trace."""
        self = super().__new__(cls)
        self._tools, self._audio, self._speaker = tools, audio, speaker
        self._trace, self._opener = trace, opener
        self._state, self._conversation_id, self._error = "connecting", None, None
        self._connected = asyncio.Event()
        self._transcript = deque(maxlen=_HISTORY)
        self._tool_calls = deque(maxlen=_HISTORY)
        return self

    async def run(self, signed_url: str, options: SessionOptions) -> None:
        """Hold the call until ElevenLabs ends it, it fails, or it is cancelled."""
        try:
            async with self._opener(signed_url, options, trace=self._trace) as conv:
                self._conversation_id = conv.metadata.conversation_id
                self._state = "live"
                self._connected.set()
                async with asyncio.TaskGroup() as group:
                    group.create_task(self._pump(conv))
                    group.create_task(self._listen(conv, group))
        except* EOFError:
            self._state = "ended"
        except* Exception as failures:
            # The call is a background task: its failure is reported, not raised.
            failure = failures.exceptions[0]
            if not isinstance(failure, _EXPECTED):
                logger.exception("Mode B call failed", exc_info=failure)
            self._state = "failed"
            self._error = str(failure) or type(failure).__name__
        finally:
            if self._state in {"connecting", "live"}:
                self._state = "ended"
            self._connected.set()
            await self._speaker.close()

    async def connected(self) -> None:
        """Return once the conversation is live or has already ended."""
        await self._connected.wait()

    def status(self) -> dict[str, object]:
        """Return the call's state and its recent exchange."""
        return {
            "state": self._state,
            "conversation_id": self._conversation_id,
            "error": self._error,
            "replayed_tool_calls": self._tools.replayed,
            "transcript": list(self._transcript),
            "tool_calls": list(self._tool_calls),
        }

    async def _pump(self, conv: Conversation) -> None:
        async for pcm in self._audio.chunks():
            await conv.send_audio(pcm)

    async def _listen(self, conv: Conversation, group: asyncio.TaskGroup) -> None:
        while True:
            match await conv.receive():
                case Audio() as audio:
                    await self._speaker.play(audio)
                case Interruption(event_id=event_id):
                    await self._speaker.interrupt(event_id)
                case ClientToolCall() as call:
                    group.create_task(self._answer(conv, call))
                case UserTranscript(text=text):
                    self._transcript.append({"role": "user", "text": text})
                case AgentResponse(text=text):
                    self._transcript.append({"role": "agent", "text": text})
                case _:
                    pass

    async def _answer(self, conv: Conversation, call: ClientToolCall) -> None:
        result, is_error = await self._tools.answer(call.tool_name, call.parameters)
        self._tool_calls.append(
            {"tool": call.tool_name, "arguments": call.parameters, "error": is_error}
        )
        if call.expects_response:
            await conv.send_tool_result(call.tool_call_id, result, is_error=is_error)
