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
- **A denied path.** A tool call aimed at the secret deny-list
  (:class:`~.secret_path_policy.SecretPathPolicy`). pi starts executing a
  tool in the same instant it announces it, so this cannot stop the read
  itself; it guarantees the turn's reply is never spoken, never reaches the
  summary, and the context that saw it is destroyed.
- **The process dying**, or the wire contract breaking.

A reply the provider fails mid-way is different: pi reports the error and
settles normally, the process is ready again, and the turn raises a plain,
transient :class:`~.session_attach.SessionAttachError` -- the human can
simply ask again.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Self, final

from punt_vox.voxd.conversation_mode.call_agent_errors import (
    CallAgentEndedError,
    CallAgentSpawnError,
    SecretPathDeniedError,
)
from punt_vox.voxd.conversation_mode.call_agent_state import AgentProc, CallAgentState
from punt_vox.voxd.conversation_mode.pi_rpc_channel import PiRpcChannel
from punt_vox.voxd.conversation_mode.reply import ReplyChunk
from punt_vox.voxd.conversation_mode.session_attach import SessionAttachError

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from pathlib import Path

    from punt_vox.voxd.conversation_mode.call_agent_launch import CallAgentLaunch
    from punt_vox.voxd.conversation_mode.pi_rpc_record import PiRpcRecord
    from punt_vox.voxd.conversation_mode.secret_path_policy import SecretPathPolicy
    from punt_vox.voxd.conversation_mode.turn import TranscribedTurn
    from punt_vox.voxd.conversation_mode.turn_timer import TurnTimer

__all__ = ["REPLY_INACTIVITY_S", "PiCallAgent"]

# Seconds with no RPC record of any kind before a turn is declared hung.
REPLY_INACTIVITY_S = 60.0


@final
class PiCallAgent:
    """One call's agent process, held from call start to call end."""

    __slots__ = ("_channel", "_policy", "_state", "_turn_timer")
    _channel: PiRpcChannel
    _policy: SecretPathPolicy
    _state: CallAgentState
    _turn_timer: TurnTimer

    def __new__(
        cls, channel: PiRpcChannel, policy: SecretPathPolicy, turn_timer: TurnTimer
    ) -> Self:
        """Wrap an already-open *channel*; the agent is dormant until spawned."""
        self = super().__new__(cls)
        self._channel = channel
        self._policy = policy
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
        agent = cls(await PiRpcChannel.open(launch, cwd), launch.policy, turn_timer)
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
        async with contextlib.aclosing(self._turn(turn.text, timed=True)) as pieces:
            async for piece in pieces:
                yield ReplyChunk(text=piece, is_final=False)
        yield ReplyChunk(text="", is_final=True)

    async def final_turn(self, prompt: str) -> str:
        """Run one last, unspoken turn (the post-call summary); return its text."""
        async with contextlib.aclosing(self._turn(prompt, timed=False)) as pieces:
            return "".join([piece async for piece in pieces])

    async def close(self) -> None:
        """End the process: ``Teardown`` when ready, ``AbortTurn`` when busy."""
        if self._state.proc is AgentProc.READY:
            self._state.teardown()
            await self._channel.close()
        elif self._state.proc is AgentProc.BUSY:
            await self._abort()

    async def _deliver_context(self, message: str) -> None:
        failure = ""
        try:
            async for record in self._records(message, timed=False):
                failure = failure or record.failure()
        except SessionAttachError as exc:
            await self._channel.kill()
            msg = f"call agent failed to start: {exc}"
            raise CallAgentSpawnError(msg) from exc
        except BaseException:
            await self._channel.kill()
            raise
        if failure:
            await self._channel.kill()
            msg = f"call agent failed to start: {failure}"
            raise CallAgentSpawnError(msg)
        self._state.spawn()

    async def _turn(self, message: str, *, timed: bool) -> AsyncGenerator[str]:
        """Run one turn from ``SendTurn`` to ``ReplyComplete``, or abort it.

        Any exit other than a settled reply -- an error, a cancellation, the
        consumer abandoning the stream -- is ``AbortTurn``: the reply in
        flight is discarded and the process killed, never left half-busy.
        """
        self._state.send_turn()
        failure = ""
        try:
            async for record in self._records(message, timed=timed):
                failure = failure or record.failure()
                if delta := record.text_delta():
                    yield delta
        except BaseException:
            await self._abort()
            raise
        self._state.reply_complete()
        if failure:
            msg = f"call agent reply failed: {failure}"
            raise SessionAttachError(msg)

    async def _records(
        self, message: str, *, timed: bool
    ) -> AsyncGenerator[PiRpcRecord]:
        """Send *message* as a prompt and yield every record until it settles."""
        await self._channel.send({"type": "prompt", "message": message})
        saw_tool = False
        while True:
            record = await self._channel.receive(REPLY_INACTIVITY_S)
            if record.touches(self._policy):
                msg = "call agent reached for a path on the secret deny-list"
                raise SecretPathDeniedError(msg)
            if record.kind == "response" and (refusal := record.failure()):
                msg = f"call agent refused the prompt: {refusal}"
                raise CallAgentEndedError(msg)
            if timed and not saw_tool and record.is_tool_event:
                saw_tool = True
                self._turn_timer.mark("first_tool_event")
            yield record
            if record.is_settled:
                return

    async def _abort(self) -> None:
        """``AbortTurn``: discard the reply in flight and kill the process."""
        if self._state.proc is AgentProc.BUSY:
            self._state.abort_turn()
        await self._channel.kill()
