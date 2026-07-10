"""Track Markdown code-fence depth while scanning a document line by line."""

from __future__ import annotations

from typing import Self, final

__all__ = ["Fence"]


@final
class Fence:
    """Tracks Markdown code-fence depth while scanning a document line by line.

    A fenced block opens on a line beginning with three or more backticks or
    tildes and closes on a bare run of the same fence character. Inside a
    fence every line is literal text, so a reconciler must not read a managed
    marker there. Feed each line's stripped form in document order;
    :attr:`inside` reports whether the following lines are fenced content.
    """

    __slots__ = ("_char",)

    # The fence character (``` `` ``` `` or ``~``) of the open block, or ``None``
    # when not inside a fence. ``None`` is the documented "no open fence" state,
    # not a failure to produce a value.
    _char: str | None

    def __new__(cls) -> Self:
        self = super().__new__(cls)
        self._char = None
        return self

    @property
    def inside(self) -> bool:
        """Return whether the scanner is currently within a fenced block."""
        return self._char is not None

    def feed(self, stripped: str) -> bool:
        """Update state from *stripped*; return True if it is a fence delimiter.

        A delimiter is the opening fence when outside a block, or the matching
        closing fence when inside one. Content lines return False.
        """
        if self._char is None:
            opener = self._opener_char(stripped)
            if opener is None:
                return False
            self._char = opener
            return True
        if self._closes(stripped):
            self._char = None
            return True
        return False

    @staticmethod
    def _opener_char(stripped: str) -> str | None:
        """Return the fence character if *stripped* opens a fence, else None.

        ``None`` is the contract for "not a fence line" (absence), matching the
        ``dict.get`` idiom -- it is not a value the caller failed to produce.
        """
        for char in ("`", "~"):
            if stripped.startswith(char * 3):
                return char
        return None

    def _closes(self, stripped: str) -> bool:
        """Return whether *stripped* is a bare closing fence for the open char.

        A closing fence is a run of the open character (three or more) with no
        trailing info string; ```` ```python ```` opens but never closes.
        """
        char = self._char
        if char is None:
            return False
        return stripped.startswith(char * 3) and stripped == char * len(stripped)
