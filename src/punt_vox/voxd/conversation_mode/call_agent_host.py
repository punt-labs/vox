"""One call's agent from spawn to summary: the call's whole agent lifecycle.

:class:`CallAgentHost` is the single place a call's agent is born and dies.
:meth:`CallAgentHost.open` reads the primary session's context snapshot,
builds the read-only launch (tool allowlist, secret deny-list, stripped
environment) and spawns the agent with that snapshot as its opening message.
For the length of the call the host lends out :attr:`CallAgentHost.agent` as
the call's :class:`~.session_attach.SessionAttach`. Then exactly one of two
endings runs:

- :meth:`CallAgentHost.finish`, for a call that ended cleanly (a hang-up, the
  inactivity timeout, the script running out): the still-ready agent writes
  the summary as its final turn, the text is persisted through
  :class:`~.call_summary.CallSummaryStore`, and the process is torn down.
- :meth:`CallAgentHost.abandon`, for a call that ended abnormally (a crash,
  Ctrl-C): the process is ended at once -- ``Teardown`` if ready,
  ``AbortTurn`` if a reply was still in flight -- with no summary, because a
  human who just interrupted the call should not wait on another model turn.

Either way the process never outlives the call.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Self, final

from punt_vox.voxd.conversation_mode.call_agent_launch import CallAgentLaunch
from punt_vox.voxd.conversation_mode.call_snapshot import CallSnapshot
from punt_vox.voxd.conversation_mode.call_summary import (
    SUMMARY_PROMPT,
    CallSummaryStore,
)
from punt_vox.voxd.conversation_mode.pi_call_agent import PiCallAgent
from punt_vox.voxd.conversation_mode.pi_turn_verdict import GuardRefusalError
from punt_vox.voxd.conversation_mode.secret_path_policy import SecretPathPolicy
from punt_vox.voxd.conversation_mode.session_attach import SessionAttachError
from punt_vox.voxd.conversation_mode.turn_timer import LoggingTurnTimer

if TYPE_CHECKING:
    from pathlib import Path
    from typing import TextIO

    from punt_vox.voxd.conversation_mode.turn_timer import TurnTimer

__all__ = ["CallAgentHost", "CallOutcome"]

logger = logging.getLogger(__name__)


@final
@dataclass(frozen=True, slots=True)
class CallOutcome:
    """Where the call's summary landed, or why there is none."""

    # Absent exactly when no summary was written; ``reason`` then says why.
    summary_path: Path | None
    reason: str = ""

    def line(self) -> str:
        """Return the one line ``vox call start`` prints as the call ends."""
        if self.summary_path is not None:
            return f"call summary: {self.summary_path}"
        return f"no call summary: {self.reason}"


@final
class CallAgentHost:
    """Own one call's agent: spawn it, lend it to the call, end it."""

    __slots__ = ("_agent", "_call_id", "_store", "_turn_timer")
    _agent: PiCallAgent
    _store: CallSummaryStore
    _call_id: str
    _turn_timer: TurnTimer

    def __new__(
        cls,
        agent: PiCallAgent,
        store: CallSummaryStore,
        *,
        call_id: str,
        turn_timer: TurnTimer,
    ) -> Self:
        self = super().__new__(cls)
        self._agent = agent
        self._store = store
        self._call_id = call_id
        self._turn_timer = turn_timer
        return self

    @classmethod
    async def open(
        cls, context: Path, *, cwd: Path, repo_root: Path, stdin: TextIO
    ) -> Self:
        """Read the snapshot at *context* and spawn the call's agent in *cwd*.

        Raises :class:`ValueError` for an unusable snapshot and
        :class:`~.call_agent_errors.CallAgentSpawnError` for an agent that
        would not start -- both before any part of the call has begun.
        """
        snapshot = CallSnapshot.read(context, stdin)
        turn_timer = LoggingTurnTimer()
        launch = CallAgentLaunch(SecretPathPolicy.for_repo(repo_root))
        agent = await PiCallAgent.spawn(
            launch,
            cwd=cwd,
            opening_message=snapshot.opening_message(),
            turn_timer=turn_timer,
        )
        store = CallSummaryStore.for_user()
        return cls(agent, store, call_id=store.new_call_id(), turn_timer=turn_timer)

    @property
    def agent(self) -> PiCallAgent:
        """The call's :class:`~.session_attach.SessionAttach`, for the call's length."""
        return self._agent

    @property
    def turn_timer(self) -> TurnTimer:
        """The one timer the whole call marks against, the agent's marks included."""
        return self._turn_timer

    async def finish(self) -> CallOutcome:
        """Collect the summary as the agent's final turn, then tear it down."""
        try:
            return await self._summarize()
        finally:
            await self._agent.close()

    async def abandon(self) -> None:
        """End the agent now, with no summary: the call did not end cleanly."""
        await self._agent.close()

    def _nothing_to_summarize(self) -> str:
        """Return why the agent cannot or need not write a summary, or ``""``."""
        if not self._agent.can_accept_turn:
            return "the call agent had already ended"
        return "" if self._agent.turns_handled else "no turns were exchanged"

    async def _summarize(self) -> CallOutcome:
        if reason := self._nothing_to_summarize():
            return CallOutcome(None, reason)
        try:
            text = await self._agent.final_turn(SUMMARY_PROMPT)
        except GuardRefusalError as exc:
            logger.warning("no call summary: %s", exc)
            return CallOutcome(None, str(exc))
        except SessionAttachError:
            logger.exception("the call agent could not write the call summary")
            return CallOutcome(None, "the call agent could not write it (see vox.log)")
        if not text.strip():
            return CallOutcome(None, "the call agent returned an empty summary")
        return CallOutcome(self._store.write(self._call_id, text))
