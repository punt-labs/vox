"""One Vox call: caller audio in, agent audio out, boss tools answered."""

from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from typing import TYPE_CHECKING, Final, Self, final

from punt_vox.voxd.voice_agent.events import (
    AgentResponse,
    AgentResponseCorrection,
    Audio,
    ClientToolCall,
    Interruption,
    UserTranscript,
)
from punt_vox.voxd.voice_call.call_log import CallLog
from punt_vox.voxd.voice_call.call_outcome import CallOutcome
from punt_vox.voxd.voice_call.progress import CallProgress
from punt_vox.voxd.voice_call.tool_reply import ToolReply

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from contextlib import AbstractAsyncContextManager

    from punt_vox.voxd.voice_agent.contracts import SessionOptions
    from punt_vox.voxd.voice_agent.session import Conversation
    from punt_vox.voxd.voice_agent.trace import EventTrace
    from punt_vox.voxd.voice_call.boss_tools import BossTools
    from punt_vox.voxd.voice_call.call_outcome import CallState
    from punt_vox.voxd.voice_call.contracts import CallInput
    from punt_vox.voxd.voice_call.progress import ProgressEvents
    from punt_vox.voxd.voice_call.progress_types import WorkerProgress
    from punt_vox.voxd.voice_call.speaker import AgentSpeaker

    type Opener = Callable[..., AbstractAsyncContextManager[Conversation]]
    # Provision the agent and return its signed URL and the session options.
    type Dial = Callable[[], Awaitable[tuple[str, SessionOptions]]]

__all__ = ["VoiceAgentCall"]

logger = logging.getLogger(__name__)

_HISTORY: Final = 20
# Failures a call can meet in normal operation (ConvAIError is a RuntimeError);
# anything else is logged as a bug.
_EXPECTED: Final = (OSError, ValueError, RuntimeError)


@final
class VoiceAgentCall:
    """Run the conversation; workers it starts outlive it."""

    __slots__ = (
        "_audio",
        "_connected",
        "_conversation_id",
        "_error",
        "_opener",
        "_progress",
        "_speaker",
        "_state",
        "_tool_calls",
        "_tools",
        "_trace",
        "_transcript",
    )
    _tools: BossTools
    _progress: CallProgress
    _audio: CallInput
    _speaker: AgentSpeaker
    _trace: EventTrace
    _opener: Opener
    _state: CallState
    _conversation_id: str | None  # None until ElevenLabs accepts the session.
    _error: str | None  # None unless the call failed.
    _connected: asyncio.Event
    _transcript: deque[tuple[str | None, dict[str, str]]]  # (response_id, turn)
    _tool_calls: deque[dict[str, object]]

    def __new__(
        cls,
        tools: BossTools,
        audio: CallInput,
        speaker: AgentSpeaker,
        *,
        trace: EventTrace,
        opener: Opener,
        progress: ProgressEvents,
    ) -> Self:
        """Bind the call's tools, caller audio, speaker, trace and peer opener."""
        self = super().__new__(cls)
        self._tools, self._audio, self._speaker = tools, audio, speaker
        self._progress, self._trace = CallProgress(progress), trace
        self._opener = opener
        self._state, self._conversation_id, self._error = "connecting", None, None
        self._connected = asyncio.Event()
        self._transcript = deque(maxlen=_HISTORY)
        self._tool_calls = deque(maxlen=_HISTORY)
        return self

    async def run(
        self,
        dial: Dial,
        covered: dict[str, int] | None = None,
        *,
        depart: Callable[[dict[str, int]], Awaitable[None]] | None = None,
    ) -> None:
        """Dial, then hold the call until it ends, fails, or is cancelled."""
        hung_up = False  # Only a remote hang-up lets the last reply play out.
        try:
            async with self._progress.subscribe() as queue:
                await self._converse(dial, queue, covered)
        except* EOFError:
            self._state = "ended"
            hung_up = True
        except* Exception as failures:
            # The call is a background task: its failure is reported, not raised.
            failure = failures.exceptions[0]
            if not isinstance(failure, _EXPECTED):
                logger.exception("Voice call failed", exc_info=failure)
            self.mark_failed(str(failure) or type(failure).__name__)
        finally:
            if self._state in {"connecting", "live"}:
                self._state = "ended"
            self._connected.set()
            try:
                if not hung_up:
                    await self._speaker.close()
            finally:
                try:
                    if depart is not None and self._conversation_id is not None:
                        await depart(self._progress.reported)
                finally:
                    if hung_up:
                        await self._speaker.finish()

    async def _converse(
        self,
        dial: Dial,
        queue: asyncio.Queue[WorkerProgress],
        covered: dict[str, int] | None,
    ) -> None:
        signed_url, options = await dial()
        async with self._opener(signed_url, options, trace=self._trace) as conv:
            self._conversation_id = conv.metadata.conversation_id
            CallLog.live(self._conversation_id)
            self._state = "live"
            self._connected.set()
            async with asyncio.TaskGroup() as group:
                group.create_task(self._pump(conv))
                group.create_task(self._progress.run(conv, queue, covered))
                group.create_task(self._listen(conv, group))

    async def connected(self) -> None:
        """Return once the conversation is live or has already ended."""
        await self._connected.wait()

    def mark_failed(self, error: str) -> None:
        """Keep the terminal failure reason in subsequent call status replies."""
        self._state, self._error = "failed", error

    def outcome(self) -> CallOutcome:
        """Return the call's state, failure reason and tool call count."""
        return CallOutcome(self._state, self._error, len(self._tool_calls))

    def status(self) -> dict[str, object]:
        """Return the call's state and its recent exchange."""
        return {
            "state": self._state,
            "conversation_id": self._conversation_id,
            "error": self._error,
            "replayed_tool_calls": self._tools.replayed,
            "transcript": [turn for _, turn in self._transcript],
            "tool_calls": list(self._tool_calls),
        }

    async def _pump(self, conv: Conversation) -> None:
        async for pcm in self._audio.chunks():
            await conv.send_audio(pcm)

    async def _listen(self, conv: Conversation, group: asyncio.TaskGroup) -> None:
        while True:
            match await conv.receive():
                case Audio() as audio:
                    self._progress.activity()
                    await self._speaker.play(audio)
                case Interruption(event_id=event_id):
                    await self._speaker.interrupt(event_id)
                case ClientToolCall() as call:
                    group.create_task(self._answer(conv, call))
                case UserTranscript(text=text):
                    self._progress.activity()
                    self._transcript.append((None, {"role": "user", "text": text}))
                case AgentResponse(text=text, response_id=response_id):
                    self._transcript.append(
                        (response_id, {"role": "agent", "text": text})
                    )
                case AgentResponseCorrection() as correction:
                    self._correct(correction)
                case _:
                    pass

    def _correct(self, correction: AgentResponseCorrection) -> None:
        """Replace interrupted agent text with what the person actually heard."""
        for response_id, turn in reversed(self._transcript):
            if response_id == correction.response_id:
                turn["text"] = correction.corrected_text
                return

    async def _answer(self, conv: Conversation, call: ClientToolCall) -> None:
        result, is_error = await self._tools.answer(call.tool_name, call.parameters)
        answer = ToolReply.from_wire(result, is_error=is_error)
        CallLog.tool(call.tool_name, answer)
        if call.tool_name == "steer_worker" and not answer.is_error:
            receipt: dict[str, object] = json.loads(answer.result)
            if receipt.get("choice") in ("approve", "deny"):
                receipt["prompt_seq"] = call.parameters.get("prompt_seq")
            self._trace.record("note", "worker_steer_receipt", receipt)
        self._tool_calls.append(
            {
                "tool": call.tool_name,
                "arguments": call.parameters,
                "error": answer.is_error,
            }
        )
        if call.expects_response:
            await conv.send_tool_result(
                call.tool_call_id, answer.result, is_error=answer.is_error
            )
