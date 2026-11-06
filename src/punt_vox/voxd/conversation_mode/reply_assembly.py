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

    A restart chunk retracts everything before it: the agent's provider
    failed that attempt and the agent is writing the reply again, so the
    failed attempt's words are no part of the reply -- not spoken, not
    saved. Text already played aloud cannot be recalled, so the spoken form
    owns up to the restart instead.
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
        """Fold *chunk* in; a restart discards the reply so far."""
        if not chunk.is_restart:
            self._pieces.append(chunk.text)
            return
        logger.info(
            "call agent restarted its reply after a failed attempt; "
            "discarded %d characters of it",
            len(self.text),
        )
        self._pieces.clear()
        self._restarted = True

    @property
    def text(self) -> str:
        """The reply as it now stands: the post-call summary saves this."""
        return "".join(self._pieces)

    @property
    def spoken(self) -> str:
        """The reply as said aloud, owning up to a restart first."""
        return f"{RESTART_PHRASE} {self.text}" if self._restarted else self.text
