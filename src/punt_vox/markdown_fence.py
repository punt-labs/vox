"""Classify a Markdown document's physical lines as top-level or code."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Self, final

__all__ = ["CodeBlockMap", "Fence"]

_MARKERS = ("`", "~")
_MIN_RUN, _INDENT = 3, 4


@final
@dataclass(frozen=True, slots=True)
class Fence:
    """A fence delimiter: *run* copies of *marker*, *bare* when nothing follows."""

    marker: str
    run: int
    bare: bool

    @classmethod
    def parse(cls, line: str) -> Fence | None:
        r"""Return the fence *line* delimits, or ``None`` when it is not a delimiter.

        ``None`` is the documented "not a fence line" contract (an absence, like
        ``dict.get``), not a failure. A delimiter is a run of three or more of
        one marker after at most three leading spaces; a tab or four or more
        leading spaces makes the line inert indented code (\S2.4). A backtick
        fence's info string cannot hold a backtick (CommonMark), so a line such
        as ```` ```x``` ```` is an inline code span, not a fence.
        """
        bare = line.rstrip("\r\n")
        if CodeBlockMap.is_indented(bare):
            return None
        stripped = bare.lstrip(" ")
        if stripped[:1] not in _MARKERS:
            return None
        marker = stripped[0]
        run = len(stripped) - len(stripped.lstrip(marker))
        if run < _MIN_RUN or (marker == "`" and "`" in stripped[run:]):
            return None
        return cls(marker, run, bare=not stripped[run:].strip(" "))

    def closes(self, opener: Fence) -> bool:
        """Return whether this delimiter closes a block *opener* started.

        Only a bare same-marker run at least the opener's closes it (an info
        string is opener-only); anything else is content; blocks never nest.
        """
        return self.bare and self.marker == opener.marker and self.run >= opener.run


@final
class CodeBlockMap:
    """Which physical lines of a document sit outside every code block.

    A line is *top-level* when Claude Code would resolve an ``@``-import written
    on it. Fenced blocks are balanced pairs (:meth:`Fence.closes`); the content
    and closing delimiter are code, the opener is not. An **unterminated opener
    runs to the end of the file**, as in Claude Code's Markdown lexer, so every
    line below it is code and :attr:`open_at_eof` is set; a line above it is
    unaffected. An indented line is always code, never a delimiter.
    """

    __slots__ = ("_open_at_eof", "_top_level")

    _open_at_eof: bool
    _top_level: tuple[bool, ...]

    def __new__(cls, lines: tuple[str, ...]) -> Self:
        """Create a code block map."""
        self = super().__new__(cls)
        code: set[int] = set()
        opener: Fence | None = None
        for i, line in enumerate(lines):
            fence = Fence.parse(line)
            if opener is not None:
                code.add(i)
                if fence is not None and fence.closes(opener):
                    opener = None
            elif fence is not None:
                opener = fence
        self._open_at_eof = opener is not None
        self._top_level = tuple(
            i not in code and not cls.is_indented(line) for i, line in enumerate(lines)
        )
        return self

    @property
    def open_at_eof(self) -> bool:
        """Return whether the document ends inside a fence that never closes."""
        return self._open_at_eof

    @property
    def top_level(self) -> tuple[bool, ...]:
        """Return one flag per physical line: ``True`` when it is top-level."""
        return self._top_level

    @staticmethod
    def is_indented(line: str) -> bool:
        """Return whether *line* is an indented-code line (tab or 4+ spaces)."""
        return line.startswith("\t") or len(line) - len(line.lstrip(" ")) >= _INDENT
