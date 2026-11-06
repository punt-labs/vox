"""The production ``SessionAttach``: one persistent ``pi --mode rpc`` agent per call.

:class:`PiCallAgent` spawns one process when the call starts, hands it the
primary session's context snapshot as its opening message, and then holds it
for the whole call -- each human turn is one ``prompt`` command on the same
live process, so the second turn reuses the first turn's warm context instead
of re-paying a cold start. It applies the call-agent Z model
(:class:`~.call_agent_state.CallAgentState`) at the moment each real event
happens: ``Spawn`` once the opening message settles, ``SendTurn`` as a prompt
goes out, ``ReplyComplete`` on ``agent_settled``, ``Teardown`` on an orderly
close, ``AbortTurn`` whenever a turn ends any other way.

Three things end a turn abnormally, and all three abort it (the process is
killed; the model makes ``ended`` terminal):

- **Silence.** No record of any kind for :data:`REPLY_INACTIVITY_S`. This is
  an inactivity bound, never a total-turn bound: a reply that is busy with
  tool calls streams tool events the whole time, so it is never mistaken for
  a hang however long it runs.
- **A denied read that ran.** The pi-side secret guard
  (``pi_extension/secret_guard.js``) is the primary control: before a tool
  executes it blocks a path outside the repo or on the deny-list
  (:class:`~.secret_path_policy.SecretPathPolicy`), and a grep over a
  directory unless every file ripgrep would search there passes; a blocked
  call is no breach -- the turn ends on the refusal, which is spoken
  (:data:`~.pi_turn_reply.GUARD_REFUSAL`), and the call goes on. As defence
  in depth, :class:`~.tool_screen.ToolScreen` re-checks every call that did
  execute; if a denied read ever ran anyway, the turn's reply is never
  spoken, never reaches the summary, and the context that saw it is destroyed.
- **The process dying**, or the wire contract breaking.

A reply the provider fails is different: pi retries it on its own, and only
the final attempt decides the turn and supplies its words
(:class:`~.pi_turn_reply.PiTurnReply`).
When every attempt fails -- or the run ends aborted with nothing said -- pi
settles normally, the process is ready again (``ReplyFailed``), and the turn
raises a plain, transient :class:`~.session_attach.SessionAttachError`; the
human can simply ask again.
"""

from __future__ import annotations

import contextlib
import logging
import re
from typing import TYPE_CHECKING, Self, final

from punt_vox.voxd.conversation_mode.call_agent_errors import (
    CallAgentEndedError,
    CallAgentSpawnError,
    SecretPathDeniedError,
)
from punt_vox.voxd.conversation_mode.call_agent_state import AgentProc, CallAgentState
from punt_vox.voxd.conversation_mode.pi_rpc_channel import PiRpcChannel
from punt_vox.voxd.conversation_mode.pi_turn_reply import PiTurnReply
from punt_vox.voxd.conversation_mode.pi_turn_verdict import (
    GuardRefusalError,
    PiTurnVerdict,
)
from punt_vox.voxd.conversation_mode.reply import ReplyChunk
from punt_vox.voxd.conversation_mode.reply_assembly import ReplyAssembly
from punt_vox.voxd.conversation_mode.session_attach import SessionAttachError
from punt_vox.voxd.conversation_mode.tool_screen import ToolScreen

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from pathlib import Path

    from punt_vox.voxd.conversation_mode.call_agent_launch import CallAgentLaunch
    from punt_vox.voxd.conversation_mode.pi_rpc_record import PiRpcRecord
    from punt_vox.voxd.conversation_mode.turn import TranscribedTurn
    from punt_vox.voxd.conversation_mode.turn_timer import TurnTimer

__all__ = ["REPLY_INACTIVITY_S", "PiCallAgent"]

logger = logging.getLogger(__name__)

# Guard refusals that mean the guard itself is degraded, not that a read
# was on the deny-list.
_DEGRADED_GUARD = re.compile(r"misconfigured|cannot list")

# Seconds with no RPC record of any kind before a turn is declared hung.
REPLY_INACTIVITY_S = 60.0


@final
class PiCallAgent:
    """One call's agent process, held from call start to call end."""

    __slots__ = ("_channel", "_screen", "_state", "_turn_timer")
    _channel: PiRpcChannel
    _screen: ToolScreen
    _state: CallAgentState
    _turn_timer: TurnTimer

    def __new__(
        cls, channel: PiRpcChannel, screen: ToolScreen, turn_timer: TurnTimer
    ) -> Self:
        """Wrap an already-open *channel*; the agent is dormant until spawned."""
        self = super().__new__(cls)
        self._channel = channel
        self._screen = screen
        self._state = CallAgentState()
        self._turn_timer = turn_timer
        return self

    @classmethod
    async def spawn(
        cls,
        launch: CallAgentLaunch,
        *,
        cwd: Path,
        opening_message: str,
        turn_timer: TurnTimer,
    ) -> Self:
        """Start the process and deliver *opening_message*: the model's ``Spawn``.

        Returns only once the opening message has settled, so a returned
        agent is ``ready`` with its context delivered -- there is no state in
        which the process exists but a turn could reach it without context.
        Any failure on the way kills the process and raises
        :class:`~.call_agent_errors.CallAgentSpawnError`.
        """
        channel = await PiRpcChannel.open(launch, cwd)
        agent = cls(channel, ToolScreen(launch.policy, cwd), turn_timer)
        await agent._deliver_context(opening_message)
        return agent

    @property
    def can_accept_turn(self) -> bool:
        """The model's ``CanAcceptTurn``."""
        return self._state.can_accept_turn

    @property
    def turns_handled(self) -> int:
        return self._state.turns_handled

    async def send_turn(self, turn: TranscribedTurn) -> AsyncGenerator[ReplyChunk]:
        """Forward *turn* and stream the reply text back as it is written."""
        async with contextlib.aclosing(self._turn(turn.text, spoken=True)) as chunks:
            async for chunk in chunks:
                yield chunk
        yield ReplyChunk(text="", is_final=True)

    async def final_turn(self, prompt: str) -> str:
        """Run one last, unspoken turn (the post-call summary); return its text.

        Raises :class:`~.pi_turn_verdict.GuardRefusalError` when the guard
        blocked a read along the way: text written around a refused read is
        incomplete, and the guard's refusal sentence is no summary either.
        """
        reply = ReplyAssembly()
        async with contextlib.aclosing(self._turn(prompt, spoken=False)) as chunks:
            async for chunk in chunks:
                reply.add(chunk)
        return reply.text

    async def close(self) -> None:
        """End the process: ``Teardown`` when ready, ``AbortTurn`` when busy."""
        if self._state.proc is AgentProc.READY:
            self._state.teardown()
            await self._channel.close()
        elif self._state.proc is AgentProc.BUSY:
            await self._abort()

    async def _deliver_context(self, message: str) -> None:
        verdict = PiTurnVerdict()
        try:
            async for record in self._records(message, timed=False):
                verdict.observe(record)
        except SessionAttachError as exc:
            await self._channel.kill()
            msg = f"call agent failed to start: {exc}"
            raise CallAgentSpawnError(msg) from exc
        except BaseException:
            await self._channel.kill()
            raise
        if verdict.failure:
            await self._channel.kill()
            msg = f"call agent failed to start: {verdict.failure}"
            raise CallAgentSpawnError(msg)
        self._state.spawn()

    async def _turn(self, message: str, *, spoken: bool) -> AsyncGenerator[ReplyChunk]:
        """Run one turn from ``SendTurn`` to ``ReplyComplete``, or abort it.

        Any exit other than a settled reply -- an error, a cancellation, the
        consumer abandoning the stream -- is ``AbortTurn``: the reply in
        flight is discarded and the process killed, never left half-busy. A
        reply that settled but failed is ``ReplyFailed``: the process lives.
        """
        self._state.send_turn()
        reply = PiTurnReply()
        try:
            async for record in self._records(message, timed=spoken):
                for chunk in reply.chunks(record):
                    yield chunk
        except BaseException:
            await self._abort()
            raise
        verdict = reply.verdict
        if verdict.failure:
            self._state.reply_failed()
            msg = f"call agent reply failed: {verdict.failure}"
            raise SessionAttachError(msg)
        self._state.reply_complete()
        if not verdict.refused:
            return
        if not spoken:
            msg = "the secret guard blocked a read this turn needed"
            raise GuardRefusalError(msg)
        yield reply.refusal()

    async def _records(
        self, message: str, *, timed: bool
    ) -> AsyncGenerator[PiRpcRecord]:
        """Send *message* as a prompt and yield every record until it settles."""
        await self._channel.send({"type": "prompt", "message": message})
        saw_tool = False
        while True:
            record = await self._channel.receive(REPLY_INACTIVITY_S)
            if self._screen.breached(record):
                msg = "a call agent tool call read a path on the secret deny-list"
                raise SecretPathDeniedError(msg)
            if record.kind == "response" and (refusal := record.failure()):
                msg = f"call agent refused the prompt: {refusal}"
                raise CallAgentEndedError(msg)
            self._log_diagnostics(record)
            if timed and not saw_tool and record.is_tool_event:
                saw_tool = True
                self._turn_timer.mark("first_tool_event")
            yield record
            if record.is_settled:
                return

    @staticmethod
    def _log_diagnostics(record: PiRpcRecord) -> None:
        """Log a guard refusal's full reason and any extension error, as seen."""
        if reason := ToolScreen.guard_refusal(record):
            # A guard that cannot list a grep's files refuses every directory
            # grep: that is degraded service, not an ordinary refusal.
            level = logging.WARNING if _DEGRADED_GUARD.search(reason) else logging.INFO
            logger.log(level, "call agent read refused: %s", reason)
        if error := record.extension_error():
            logger.error("call agent extension error: %s", error)

    async def _abort(self) -> None:
        """``AbortTurn``: discard the reply in flight and kill the process."""
        if self._state.proc is AgentProc.BUSY:
            self._state.abort_turn()
        await self._channel.kill()
