"""Tests for :class:`CallAgentState`, named for the call-agent Z model.

The model is ``docs/conversation-mode-call-agent.tex``.

Each test names the Z operation or invariant it checks, so a failure points
straight at the schema it breaks.
"""

from __future__ import annotations

from collections.abc import Callable
from itertools import product

import pytest

from punt_vox.voxd.conversation_mode.call_agent_state import (
    AgentProc,
    CallAgentState,
    IllegalAgentTransitionError,
)

type Operation = Callable[[CallAgentState], None]

_OPERATIONS: dict[str, Operation] = {
    "Spawn": CallAgentState.spawn,
    "SendTurn": CallAgentState.send_turn,
    "ReplyComplete": CallAgentState.reply_complete,
    "Teardown": CallAgentState.teardown,
    "AbortTurn": CallAgentState.abort_turn,
}


def _ready() -> CallAgentState:
    state = CallAgentState()
    state.spawn()
    return state


def _busy() -> CallAgentState:
    state = _ready()
    state.send_turn()
    return state


def _assert_invariants(state: CallAgentState) -> None:
    """The ``CallAgentState`` schema predicate, all three clauses."""
    if state.proc in {AgentProc.READY, AgentProc.BUSY}:
        assert state.context_delivered
    if state.proc is not AgentProc.DORMANT:
        assert state.read_only
    if state.proc is AgentProc.DORMANT:
        assert state.turns_handled == 0


class TestInit:
    def test_init_is_dormant_with_no_context_and_no_turns(self) -> None:
        state = CallAgentState()
        assert (
            state.proc,
            state.context_delivered,
            state.read_only,
            state.turns_handled,
        ) == (AgentProc.DORMANT, False, False, 0)


class TestSpawn:
    def test_spawn_delivers_context_and_read_only_in_the_same_step(self) -> None:
        state = _ready()
        assert (state.proc, state.context_delivered, state.read_only) == (
            AgentProc.READY,
            True,
            True,
        )

    def test_spawn_twice_is_illegal(self) -> None:
        with pytest.raises(IllegalAgentTransitionError, match="Spawn"):
            _ready().spawn()


class TestSendTurn:
    def test_send_turn_moves_ready_to_busy(self) -> None:
        assert _busy().proc is AgentProc.BUSY

    def test_send_turn_while_busy_is_refused(self) -> None:
        """The spike's "Agent is already processing" -- refused before the wire."""
        with pytest.raises(IllegalAgentTransitionError, match="SendTurn"):
            _busy().send_turn()

    def test_send_turn_before_spawn_is_refused(self) -> None:
        with pytest.raises(IllegalAgentTransitionError):
            CallAgentState().send_turn()


class TestReplyComplete:
    def test_reply_complete_returns_to_ready_and_counts_the_turn(self) -> None:
        state = _busy()
        state.reply_complete()
        assert (state.proc, state.turns_handled) == (AgentProc.READY, 1)

    def test_reply_complete_from_ready_is_illegal(self) -> None:
        with pytest.raises(IllegalAgentTransitionError, match="ReplyComplete"):
            _ready().reply_complete()


class TestTeardown:
    def test_teardown_from_ready_ends_the_process(self) -> None:
        state = _ready()
        state.teardown()
        assert state.proc is AgentProc.ENDED

    def test_teardown_from_busy_is_forbidden(self) -> None:
        """A streaming reply is never killed by Teardown -- only AbortTurn."""
        with pytest.raises(IllegalAgentTransitionError, match="Teardown"):
            _busy().teardown()

    def test_teardown_from_dormant_is_forbidden(self) -> None:
        with pytest.raises(IllegalAgentTransitionError):
            CallAgentState().teardown()


class TestAbortTurn:
    def test_abort_turn_ends_a_busy_process(self) -> None:
        state = _busy()
        state.abort_turn()
        assert state.proc is AgentProc.ENDED

    def test_abort_turn_from_ready_is_illegal(self) -> None:
        with pytest.raises(IllegalAgentTransitionError, match="AbortTurn"):
            _ready().abort_turn()


class TestCanAcceptTurn:
    @pytest.mark.parametrize(
        ("build", "expected"),
        [
            (CallAgentState, False),
            (_ready, True),
            (_busy, False),
        ],
    )
    def test_can_accept_turn_iff_ready(
        self, build: Callable[[], CallAgentState], *, expected: bool
    ) -> None:
        assert build().can_accept_turn is expected


class TestEnded:
    @pytest.mark.parametrize("name", list(_OPERATIONS))
    def test_ended_is_terminal(self, name: str) -> None:
        state = _ready()
        state.teardown()
        with pytest.raises(IllegalAgentTransitionError):
            _OPERATIONS[name](state)


class TestReadOnlyOverTheWholeLiveExtent:
    """Invariant 2: read-only from Spawn to the end, never toggled per turn."""

    @pytest.mark.parametrize("sequence", list(product(_OPERATIONS, repeat=4)))
    def test_every_reachable_state_satisfies_the_schema(
        self, sequence: tuple[str, ...]
    ) -> None:
        """Every operation sequence of length 4 keeps all three invariants."""
        state = CallAgentState()
        _assert_invariants(state)
        for name in sequence:
            try:
                _OPERATIONS[name](state)
            except IllegalAgentTransitionError:
                continue
            _assert_invariants(state)
