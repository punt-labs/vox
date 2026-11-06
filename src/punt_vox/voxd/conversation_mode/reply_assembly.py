"""Assemble a streamed reply's text, keeping only the attempt that stands."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Self, final

if TYPE_CHECKING:
    from punt_vox.voxd.conversation_mode.reply import ReplyChunk

__all__ = ["RESTART_PHRASE", "ReplyAssembly"]

logger = logging.getLogger(__name__)

# Spoken ahead of a reply the agent had to write again from the start.
RESTART_PHRASE = "Sorry, let me start that again."


@final
class ReplyAssembly:
    """The text of one turn's reply, folded chunk by chunk.

    A retracting chunk withdraws the text of a message the agent's provider
    failed: the agent is writing that message again, so its failed words
    are no part of the reply -- not spoken, not saved. Text already played
    aloud cannot be recalled, so the spoken form owns up to the restart.
    """

    __slots__ = ("_pieces", "_restarted")
    _pieces: list[str]
    _restarted: bool

    def __new__(cls) -> Self:
        self = super().__new__(cls)
        self._pieces = []
        self._restarted = False
        return self

    def add(self, chunk: ReplyChunk) -> None:
        """Fold *chunk* in, withdrawing what it retracts first."""
        if chunk.retracts:
            logger.info(
                "call agent retried a failed message; discarded %d characters of it",
                chunk.retracts,
            )
            kept = self.text[: -chunk.retracts]
            self._pieces[:] = [kept]
            self._restarted = True
        self._pieces.append(chunk.text)

    @property
    def text(self) -> str:
        """The reply as it now stands: the post-call summary saves this."""
        return "".join(self._pieces)

    @property
    def spoken(self) -> str:
        """The reply as said aloud, owning up to a restart first."""
        return f"{RESTART_PHRASE} {self.text}" if self._restarted else self.text
