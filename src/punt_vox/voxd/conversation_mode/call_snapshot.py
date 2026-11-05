"""The context snapshot a call agent is spawned with, as the primary session wrote it.

The call agent does not resume the developer's interactive session; it starts
fresh and is handed a compact snapshot instead -- the topic, the current
task, recent conversation, and any quarry or docs lookups -- written by that
primary session (``/vox:call start <topic>``) and passed to ``vox call start
--context``. :class:`CallSnapshot` validates it and frames it as the agent's
opening message, the one ``Spawn`` delivers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Self, final

if TYPE_CHECKING:
    from pathlib import Path
    from typing import TextIO

__all__ = ["CallSnapshot"]

# A snapshot is a briefing, not a corpus: past this it is a sign something
# pasted a whole file in, which would only slow the agent's first turn.
_MAX_BYTES = 64 * 1024

# ``--context -`` reads the snapshot from stdin instead of a file.
_STDIN = "-"

_OPENING = (
    "A voice call is starting. Below is a context snapshot from the "
    "developer's main working session: what they are working on and what "
    "they want to talk through. Read it, then reply with one short spoken "
    "sentence saying you are ready. Do not use any tools yet."
)


@final
class CallSnapshot:
    """A validated context snapshot, ready to open a call agent with."""

    __slots__ = ("_text",)
    _text: str

    def __new__(cls, text: str) -> Self:
        stripped = text.strip()
        if not stripped:
            msg = "the context snapshot is empty"
            raise ValueError(msg)
        size = len(stripped.encode())
        if size > _MAX_BYTES:
            msg = f"the context snapshot is {size} bytes; the limit is {_MAX_BYTES}"
            raise ValueError(msg)
        self = super().__new__(cls)
        self._text = stripped
        return self

    @classmethod
    def read(cls, source: Path, stdin: TextIO) -> Self:
        """Read the snapshot from *source*, or from *stdin* when *source* is ``-``.

        Raises :class:`ValueError` for an unreadable, empty, or oversized
        snapshot -- an expected usage error the CLI reports cleanly.
        """
        try:
            text = stdin.read() if str(source) == _STDIN else source.read_text()
        except (OSError, UnicodeDecodeError) as exc:
            msg = f"cannot read the context snapshot from {source}: {exc}"
            raise ValueError(msg) from exc
        return cls(text)

    @property
    def text(self) -> str:
        return self._text

    def opening_message(self) -> str:
        """Return the call agent's opening message: framing plus the snapshot."""
        return f"{_OPENING}\n\n<context-snapshot>\n{self._text}\n</context-snapshot>"
