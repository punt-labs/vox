"""One Vox call: caller audio in, agent audio out, boss tools answered."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Final, Self, final

from punt_vox.voxd.log_text import LogText
from punt_vox.voxd.voice_agent.events import (
    AgentResponse,
    AgentResponseCorrection,
    AgentToolResponse,
    Audio,
    ClientToolCall,
    Interruption,
    UserTranscript,
)
from punt_vox.voxd.voice_agent.fault import ConvAIError
from punt_vox.voxd.voice_call.boss_tools import BossTools
from punt_vox.voxd.voice_call.call_log import CallLog
from punt_vox.voxd.voice_call.call_outcome import CallEnd, CallOutcome
from punt_vox.voxd.voice_call.progress import CallProgress
from punt_vox.voxd.voice_call.tool_reply import ToolReply
from punt_vox.voxd.voice_call.tool_trace import ToolTrace
from punt_vox.voxd.voice_call.transcript import CallTranscript

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Awaitable, Callable
    from contextlib import AbstractAsyncContextManager

    from punt_vox.voxd.voice_agent.contracts import SessionOptions
    from punt_vox.voxd.voice_agent.session import Conversation
    from punt_vox.voxd.voice_agent.trace import EventTrace
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
        "_ended_by",
        "_error",
        "_opener",
        "_progress",
        "_speaker",
        "_state",
        "_tool_calls",
        "_tools",
        "_tools_answered",
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
    _ended_by: CallEnd | None  # None until the call ended without failing.
    _connected: asyncio.Event
    _transcript: CallTranscript
    _tool_calls: list[dict[str, object]]  # Every one the call made, for the report.
    _tools_answered: int  # Every one, for the end line; the history is capped.

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
        self._progress = CallProgress(progress, tools.guard.provenance)
        self._trace = trace
        self._opener = opener
        self._state, self._conversation_id, self._error = "connecting", None, None
        self._ended_by = None
        self._connected = asyncio.Event()
        self._transcript = CallTranscript()
        self._tool_calls, self._tools_answered = [], 0
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
            self._hang_up(CallEnd.PEER)
            hung_up = True
        except* ConvAIError as faults:
            # After the agent's end_call the provider closes the conversation;
            # that close is the agent hanging up, whatever close code it uses.
            if self._ended_by is CallEnd.AGENT:
                details = [str(fault) for fault in faults.exceptions]
                self._trace.record("note", "agent_end_close", {"faults": details})
                self._hang_up(CallEnd.AGENT)
                hung_up = True
            else:
                self.mark_failed(self._failure_text(faults.exceptions[0]))
        except* Exception as failures:
            # The call is a background task: its failure is reported, not raised.
            failure = failures.exceptions[0]
            if not isinstance(failure, _EXPECTED):
                logger.exception("Voice call failed", exc_info=failure)
            self.mark_failed(self._failure_text(failure))
        finally:
            await self._settle(depart, hung_up=hung_up)

    def _hang_up(self, ended_by: CallEnd) -> None:
        """End the call without failing; the agent's end_call stays who ended it."""
        self._state = "ended"
        if self._ended_by is None:
            self._ended_by = ended_by

    @staticmethod
    def _failure_text(failure: Exception) -> str:
        # Notes carry what cleanup could not do, so the caller must see them.
        return (str(failure) or type(failure).__name__) + LogText.notes(failure)

    async def _settle(
        self,
        depart: Callable[[dict[str, int]], Awaitable[None]] | None,
        *,
        hung_up: bool,
    ) -> None:
        """End the call: release the speaker, then depart, then play out a hang-up."""
        if self._state in {"connecting", "live"}:
            self._hang_up(CallEnd.CALLER)
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
                    await self._play_out()

    async def _play_out(self) -> None:
        """Let the last reply finish; a device that never played it fails the call."""
        if (unplayed := await self._speaker.finish()) is not None:
            self.mark_failed(unplayed)

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
                group.create_task(self._relay(conv, queue, covered))
                group.create_task(self._listen(conv, group))

    async def connected(self) -> None:
        """Return once the conversation is live or has already ended."""
        await self._connected.wait()

    def mark_failed(self, error: str) -> None:
        """Keep the failure for status replies; a failure is nobody's hang-up."""
        self._state, self._error, self._ended_by = "failed", error, None

    def mark_ended(self) -> None:
        """End a call whose task was cancelled before :meth:`run` ever began.

        Nothing was dialled and nothing is held by the call itself, so the
        only work is the terminal state a status reply reports.
        """
        self._state, self._ended_by = "ended", CallEnd.CALLER
        self._connected.set()

    def outcome(self) -> CallOutcome:
        """Return the call's state, failure reason, tool call count and who ended it."""
        return CallOutcome(
            self._state, self._error, self._tools_answered, self._ended_by
        )

    def status(self) -> dict[str, object]:
        """Return the call's state, whole transcript and every tool call."""
        return {
            "state": self._state,
            "conversation_id": self._conversation_id,
            "error": self._error,
            "notes": list(self._speaker.notes),
            "ended_by": None if self._ended_by is None else self._ended_by.value,
            "replayed_tool_calls": self._tools.replayed,
            "transcript": self._transcript.turns(),
            "tool_calls": list(self._tool_calls),
        }

    async def _pump(self, conv: Conversation) -> None:
        async with self._until_closed("caller_audio"):
            async for pcm in self._audio.chunks():
                await conv.send_audio(pcm)

    async def _relay(
        self,
        conv: Conversation,
        queue: asyncio.Queue[WorkerProgress],
        covered: dict[str, int] | None,
    ) -> None:
        async with self._until_closed("worker_progress"):
            await self._progress.run(conv, queue, covered)

    @asynccontextmanager
    async def _until_closed(self, writer: str) -> AsyncGenerator[None]:
        """Stop a writer when the peer closes under it; the listener ends the call.

        A send that fails because the peer closed is not the call's end. The
        frames the agent sent before closing, its end_call acknowledgement
        among them, are still queued for the listener, which reaches the
        close itself. A writer's failure cancelling the listener would drop
        them, and the call would end as a peer hang-up instead.
        """
        try:
            yield
        except (EOFError, ConvAIError) as exc:
            self._trace.record("note", f"{writer}_stopped", {"reason": str(exc)})

    async def _listen(self, conv: Conversation, group: asyncio.TaskGroup) -> None:
        while True:
            match await conv.receive():
                case Audio() as audio:
                    self._progress.activity()
                    await self._speaker.play(audio)
                case Interruption(event_id=event_id):
                    await self._speaker.interrupt(event_id)
                case ClientToolCall() as call:
                    self._speaker.note_tool_call()
                    group.create_task(self._answer(conv, call))
                case UserTranscript() as turn:
                    self._progress.activity()
                    self._transcript.record(turn)
                    self._tools.guard.gate.heard(turn.text)
                case AgentResponse() | AgentResponseCorrection() as turn:
                    self._heard_agent(turn)
                case AgentToolResponse() as ack:
                    self._acknowledged(ack)

    def _heard_agent(self, turn: AgentResponse | AgentResponseCorrection) -> None:
        """Record what the agent said."""
        self._transcript.record(turn)
        self._tools.guard.gate.agent_spoke(
            turn.text if isinstance(turn, AgentResponse) else turn.corrected_text
        )

    def _acknowledged(self, ack: AgentToolResponse) -> None:
        """Taint on a tool voxd did not run; note end_call, the agent's hang-up."""
        self._tools.guard.provenance.observe_tool_response(
            ack.tool_name or "", ack.tool_type, self._tools.tool_names()
        )
        if ack.ends_call:
            self._ended_by = CallEnd.AGENT
            self._trace.record(
                "note", "agent_end_call", {"tool_call_id": ack.tool_call_id}
            )

    async def _answer(self, conv: Conversation, call: ClientToolCall) -> None:
        result, is_error = await self._tools.answer(call.tool_name, call.parameters)
        try:
            answer = ToolReply.from_wire(result, is_error=is_error)
        except ValueError as exc:  # JSONDecodeError included: the call survives.
            CallLog.unreadable(call.tool_name, exc)
            answer = ToolReply.failed("tool reply was unreadable")
        CallLog.tool(call.tool_name, answer)
        self._tool_calls.append(
            {
                "tool": call.tool_name,
                "arguments": call.parameters
                if BossTools.keeps_arguments(call.tool_name)
                else {},
                "error": answer.is_error,
            }
        )
        self._tools_answered += 1
        try:
            if call.expects_response:
                await conv.send_tool_result(
                    call.tool_call_id, answer.result, is_error=answer.is_error
                )
        finally:
            if not answer.is_error:
                ToolTrace(self._trace).record(call, answer.result)
