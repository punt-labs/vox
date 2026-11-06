"""Where a call's summary is written, and what the call agent is asked to write.

When a call ends cleanly, the call agent authors a structured summary as its
own final turn (:data:`SUMMARY_PROMPT`) -- it is the one that was on the
call, so a second synthesis pass by another process would only lose detail.
The agent is read-only, so it writes nothing itself: :class:`CallSummaryStore`
persists its text to ``calls_dir() / "<call-id>-summary.md"``, which the
primary session that started the call reads once ``vox call start`` returns.

The sections follow the meeting-summary shape ``/prfaq:meeting`` uses.
*Action Items* is the handoff that matters: it is how the call agent's
read-only findings become the primary session's writes, after the call.

Retention: the newest :data:`KEEP_SUMMARIES` summaries are kept and older
ones pruned on every write. A summary is a handoff, read once by the session
that started the call; a bounded history keeps recent calls revisitable
without letting ``calls_dir()`` -- which holds whatever every call discussed
-- grow without limit.
"""

from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Self, final

from punt_vox.atomic_file import AtomicFile
from punt_vox.paths import calls_dir
from punt_vox.private_state import PrivateState
from punt_vox.voxd.conversation_mode.reply_redaction import reply_redactor

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["KEEP_SUMMARIES", "SUMMARY_PROMPT", "CallSummaryStore"]

logger = logging.getLogger(__name__)

KEEP_SUMMARIES = 50

_SUFFIX = "-summary.md"

SUMMARY_PROMPT = (
    "The call has ended. Write the call summary now. It is saved to a file "
    "for the developer's main session to act on, not spoken, so write "
    "markdown. Use exactly these five sections, in this order, each a "
    "level-2 heading: '## Decisions Made', '## Action Items', "
    "'## Context Gathered', '## Beads Touched', '## Notes / Open Questions'. "
    "Use bullets under each, and write 'None.' under an empty section. "
    "Action Items are what the main session should do next -- be specific: "
    "file paths and the change to make; you could not make changes yourself. "
    "Context Gathered lists the files you read and the snapshot material you "
    "relied on. Beads Touched lists any bead IDs created or updated during "
    "the call. Never include a secret value. Start directly with the first "
    "heading."
)


@final
class CallSummaryStore:
    """The directory of post-call summaries, newest :data:`KEEP_SUMMARIES` kept."""

    __slots__ = ("_keep", "_root")
    _root: Path
    _keep: int

    def __new__(cls, root: Path, *, keep: int = KEEP_SUMMARIES) -> Self:
        if keep < 1:
            msg = f"must keep at least one summary, not {keep}"
            raise ValueError(msg)
        self = super().__new__(cls)
        self._root = root
        self._keep = keep
        return self

    @classmethod
    def for_user(cls) -> Self:
        """Return the store at this user's ``calls_dir()``."""
        return cls(calls_dir())

    @staticmethod
    def new_call_id() -> str:
        """Return a fresh call id: a UTC timestamp, so ids sort chronologically."""
        return f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{secrets.token_hex(3)}"

    def path_for(self, call_id: str) -> Path:
        return self._root / f"{call_id}{_SUFFIX}"

    def write(self, call_id: str, body: str) -> Path:
        """Write *body*, redacted, as *call_id*'s summary (0600, atomically); prune.

        A summary outlives the call on disk, so a secret the agent quoted in it
        is masked as it would be in speech (:data:`~.reply_redaction.reply_redactor`).
        """
        path = self.path_for(call_id)
        summary = reply_redactor(body.strip())
        document = f"# Call Summary\n**Call:** {call_id}\n\n{summary}\n"
        PrivateState(path).ensure_private_tree()
        AtomicFile(path).replace(document, mode=0o600)
        self._prune()
        return path

    def _prune(self) -> None:
        """Remove all but the newest summaries; a failure is logged, never raised.

        The summary itself is already written: a stale one that will not go
        away must not cost the call its handoff.
        """
        summaries = sorted(self._root.glob(f"*{_SUFFIX}"))
        for stale in summaries[: -self._keep]:
            try:
                stale.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning("could not prune call summary %s: %s", stale, exc)
