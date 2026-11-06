"""Turn one call-agent turn's RPC records into the reply chunks a caller sees."""

from __future__ import annotations

from typing import TYPE_CHECKING, Self, final

from punt_vox.voxd.conversation_mode.pi_turn_verdict import PiTurnVerdict
from punt_vox.voxd.conversation_mode.reply import ReplyChunk

if TYPE_CHECKING:
    from punt_vox.voxd.conversation_mode.pi_rpc_record import PiRpcRecord

__all__ = ["GUARD_REFUSAL", "PiTurnReply"]

# Spoken when the secret guard blocked a read and the turn ended on it.
GUARD_REFUSAL = (
    "I can't open that -- it's outside the repository or may hold secrets, "
    "and anything I read on a call is spoken aloud."
)


@final
class PiTurnReply:
    """One turn's reply as it streams, and the verdict its records add up to.

    Only the final attempt's words may be spoken or saved: when pi retries a
    message that already wrote text, a chunk retracts that message's text --
    and only it; pi keeps the turn's earlier messages -- before the retry
    writes its own.
    """

    __slots__ = ("_verdict",)
    _verdict: PiTurnVerdict

    def __new__(cls) -> Self:
        self = super().__new__(cls)
        self._verdict = PiTurnVerdict()
        return self

    @property
    def verdict(self) -> PiTurnVerdict:
        """What the records seen so far decide about the turn."""
        return self._verdict

    def chunks(self, record: PiRpcRecord) -> tuple[ReplyChunk, ...]:
        """Fold *record* into the verdict; return the reply chunks it carries."""
        # Ask before observing: a retry start forgets the failed message's text.
        failed = self._verdict.unkept if record.is_retry_start else 0
        self._verdict.observe(record)
        chunks: list[ReplyChunk] = []
        if failed:
            chunks.append(ReplyChunk(text="", is_final=False, retracts=failed))
        if delta := record.text_delta():
            chunks.append(ReplyChunk(text=delta, is_final=False))
        return tuple(chunks)

    def refusal(self) -> ReplyChunk:
        """Return the guard's refusal, said after whatever the reply did say.

        The guard ends the turn the moment it blocks, so the model never gets
        to explain why.
        """
        text = f" {GUARD_REFUSAL}" if self._verdict.spoke else GUARD_REFUSAL
        return ReplyChunk(text=text, is_final=False)
