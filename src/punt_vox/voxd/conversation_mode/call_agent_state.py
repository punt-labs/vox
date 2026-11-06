"""The call agent's process lifecycle, as the call-agent Z model states it.

The model is ``docs/conversation-mode-call-agent.tex``.

:class:`CallAgentState` is the Z schema ``CallAgentState`` made executable:
four process states (:class:`AgentProc`), six operations (``Spawn``,
``SendTurn``, ``ReplyComplete``, ``ReplyFailed``, ``Teardown``,
``AbortTurn``), one
observation (``CanAcceptTurn``), and the schema's three invariants held by
construction rather than checked after the fact. Every operation checks its
precondition and raises :class:`IllegalAgentTransitionError` when it fails --
a caller that sends a turn while another is in flight has a bug, the exact
rejection ``pi --mode rpc`` itself answers with ("Agent is already
processing"), so it is refused here before it ever reaches the process.

The class knows nothing about processes or pipes. The process host
(:class:`~.pi_call_agent.PiCallAgent`) applies an operation at the moment
the real-world event it names happens, so the model's invariants describe
the host's actual behaviour, not a parallel bookkeeping copy of it.
"""

from __future__ import annotations

from enum import Enum, auto
from typing import Self, final

__all__ = ["AgentProc", "CallAgentState", "IllegalAgentTransitionError"]


class AgentProc(Enum):
    """The model's ``AgentProc`` free type: where the call agent process is."""

    DORMANT = auto()
    """No process spawned yet."""
    READY = auto()
    """Alive, context delivered, waiting for a prompt."""
    BUSY = auto()
    """A prompt is in flight; its reply has not settled."""
    ENDED = auto()
    """Torn down or aborted. Terminal."""


class IllegalAgentTransitionError(ValueError):
    """An operation's precondition on :attr:`CallAgentState.proc` failed."""


@final
class CallAgentState:
    """One call agent's lifecycle state, with the model's invariants built in.

    ``context_delivered`` and ``read_only`` are not independent flags a caller
    could set: ``Spawn`` is the only operation that sets them, and it sets
    both together with ``proc = READY``, so invariants 1 (context is present
    whenever the process can take or is taking a turn) and 2 (read-only for
    the process's whole live extent, never toggled per turn) cannot be
    broken by any sequence of operations.
    """

    __slots__ = ("_context_delivered", "_proc", "_read_only", "_turns_handled")
    _proc: AgentProc
    _context_delivered: bool
    _read_only: bool
    _turns_handled: int

    def __new__(cls) -> Self:
        """Return the model's ``Init`` state: dormant, no context, no turns."""
        self = super().__new__(cls)
        self._proc = AgentProc.DORMANT
        self._context_delivered = False
        self._read_only = False
        self._turns_handled = 0
        return self

    @property
    def proc(self) -> AgentProc:
        return self._proc

    @property
    def context_delivered(self) -> bool:
        return self._context_delivered

    @property
    def read_only(self) -> bool:
        return self._read_only

    @property
    def turns_handled(self) -> int:
        return self._turns_handled

    @property
    def can_accept_turn(self) -> bool:
        """The model's ``CanAcceptTurn``: true exactly when ``proc`` is ready."""
        return self._proc is AgentProc.READY

    def spawn(self) -> None:
        """``Spawn``: dormant -> ready, delivering context and read-only at once."""
        self._require("Spawn", AgentProc.DORMANT)
        self._proc = AgentProc.READY
        self._context_delivered = True
        self._read_only = True
        self._turns_handled = 0

    def send_turn(self) -> None:
        """``SendTurn``: ready -> busy. Never while a reply is still in flight."""
        self._require("SendTurn", AgentProc.READY)
        self._proc = AgentProc.BUSY

    def reply_complete(self) -> None:
        """``ReplyComplete``: busy -> ready, counting the turn handled."""
        self._require("ReplyComplete", AgentProc.BUSY)
        self._proc = AgentProc.READY
        self._turns_handled += 1

    def reply_failed(self) -> None:
        """``ReplyFailed``: busy -> ready. The process lives; no turn was handled."""
        self._require("ReplyFailed", AgentProc.BUSY)
        self._proc = AgentProc.READY

    def teardown(self) -> None:
        """``Teardown``: ready -> ended. Never from busy -- that is ``AbortTurn``."""
        self._require("Teardown", AgentProc.READY)
        self._proc = AgentProc.ENDED

    def abort_turn(self) -> None:
        """``AbortTurn``: busy -> ended, discarding the reply in flight."""
        self._require("AbortTurn", AgentProc.BUSY)
        self._proc = AgentProc.ENDED

    def _require(self, operation: str, allowed: AgentProc) -> None:
        if self._proc is not allowed:
            msg = (
                f"{operation} requires the call agent to be {allowed.name.lower()}, "
                f"not {self._proc.name.lower()}"
            )
            raise IllegalAgentTransitionError(msg)
