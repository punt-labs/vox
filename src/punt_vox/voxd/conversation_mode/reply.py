"""One chunk of a streamed agent reply."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["ReplyChunk"]


@dataclass(frozen=True, slots=True)
class ReplyChunk:
    """A piece of the agent's reply, delivered as it becomes available.

    FR-11 requires speaking to begin on the reply's first complete portion,
    not the whole reply -- so a :class:`~.session_attach.SessionAttach`
    implementation streams a sequence of these rather than returning one
    finished string. ``is_final`` marks the last chunk of a given turn's
    reply; a caller (the sentence-streamed synthesis pipeline) uses it to
    know when the agent has finished, not to infer completion from the
    stream simply ending. ``is_restart`` marks a textless chunk that
    retracts every chunk before it: the attempt that wrote them failed and
    the agent is writing the reply again from the start.
    """

    text: str
    is_final: bool
    is_restart: bool = False
