"""What one call-agent turn's RPC records add up to: a reply, a failure, a refusal.

A turn is not decided by any single record. pi retries a provider failure on
its own (``auto_retry_start`` ... ``auto_retry_end``), so an errored
``message_end`` mid-turn means nothing until the retry settles; a run can end
``aborted`` with nothing said; and the pi-side secret guard can end a turn
on a refusal the model never gets to explain. :class:`PiTurnVerdict` folds
the records of one turn, in order, into the three answers the call agent
needs once the turn settles: did it fail, and why; was a read refused; did
it say anything.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Self, final

from punt_vox.voxd.conversation_mode.session_attach import SessionAttachError
from punt_vox.voxd.conversation_mode.tool_screen import ToolScreen

if TYPE_CHECKING:
    from punt_vox.voxd.conversation_mode.pi_rpc_record import PiRpcRecord

__all__ = ["GuardRefusalError", "PiTurnVerdict"]


class GuardRefusalError(SessionAttachError):
    """The secret guard blocked a read an unspoken turn needed.

    Transient -- nothing was read and the agent is still ready -- but the
    turn's text is incomplete, so it must not stand as the turn's result:
    a post-call summary written around a refused read is no summary.
    """


@final
class PiTurnVerdict:
    """One turn's outcome, decided by its final attempt."""

    __slots__ = ("_aborted", "_failure", "_kept", "_refused", "_unkept")
    _failure: str
    _aborted: bool
    _refused: bool
    # Reply characters written by messages that ended, and by the open one.
    _kept: int
    _unkept: int

    def __new__(cls) -> Self:
        self = super().__new__(cls)
        self._failure = ""
        self._aborted = False
        self._refused = False
        self._kept = 0
        self._unkept = 0
        return self

    def observe(self, record: PiRpcRecord) -> None:
        """Fold the next record of the turn into the verdict."""
        if record.is_retry_start:
            # pi drops only the failed message and writes it again: forget its
            # failure and its words; the turn's earlier messages stand.
            self._failure, self._unkept = "", 0
        elif failure := record.failure():
            self._failure = failure
        elif record.ends_reply_message:
            self._kept, self._unkept = self._kept + self._unkept, 0
        self._unkept += len(record.text_delta())
        self._refused = self._refused or bool(ToolScreen.guard_refusal(record))
        self._aborted = self._aborted or record.is_aborted

    @property
    def failure(self) -> str:
        """Why the turn failed, or ``""`` when its reply stands."""
        if self._failure:
            return self._failure
        if self._aborted and not self.spoke:
            return "the reply was aborted before any of it was written"
        return ""

    @property
    def refused(self) -> bool:
        """Whether the pi-side guard blocked a read during the turn."""
        return self._refused

    @property
    def spoke(self) -> bool:
        """Whether any reply text of the turn still stands."""
        return bool(self._kept or self._unkept)

    @property
    def unkept(self) -> int:
        """Reply characters the open message wrote, which a retry would drop."""
        return self._unkept
