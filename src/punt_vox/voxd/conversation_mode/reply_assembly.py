"""Assemble a streamed reply's text, keeping only the attempt that stands."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Self, final

if TYPE_CHECKING:
    from punt_vox.voxd.conversation_mode.reply import ReplyChunk

__all__ = ["ReplyAssembly"]

logger = logging.getLogger(__name__)


@final
class ReplyAssembly:
    """The text of one turn's reply, folded chunk by chunk.

    A retracting chunk withdraws the text of a message the agent's provider
    failed: the agent is writing that message again, so its failed words
    are no part of the reply -- not spoken, not saved. A call speaks the reply
    only once the turn settles, so retracted text was never heard.
    """

    __slots__ = ("_pieces",)
    _pieces: list[str]

    def __new__(cls) -> Self:
        self = super().__new__(cls)
        self._pieces = []
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
        self._pieces.append(chunk.text)

    @property
    def text(self) -> str:
        """The reply as it now stands: spoken on a call, saved as a summary."""
        return "".join(self._pieces)
