"""Find and remove a bare ``@``-import line among a Markdown doc's top-level lines."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, ClassVar, Self, final

from punt_vox.markdown_fence import CodeBlockMap

if TYPE_CHECKING:
    from collections.abc import Iterator

__all__ = ["MarkdownDoc"]


@final
class MarkdownDoc:
    """A Markdown document that knows which of its physical lines are top-level.

    :class:`~punt_vox.markdown_fence.CodeBlockMap` classifies the lines once at
    construction; :meth:`contains` and :meth:`without` are the two queries the
    import writer needs, and :meth:`with_inserted` places a tool-owned import
    in sorted order among sibling tool imports (TED-13) while refusing to write
    into a fence that never closes (TED-14).
    """

    __slots__ = ("_blocks", "_lines")

    _blocks: CodeBlockMap
    _lines: tuple[str, ...]

    _TOOL_IMPORT: ClassVar[re.Pattern[str]] = re.compile(
        r"@\.punt-labs/(?:local/)?[a-z0-9-]+/CLAUDE\.md"
    )

    def __new__(cls, text: str) -> Self:
        """Create a markdown doc."""
        self = super().__new__(cls)
        self._lines = tuple(text.splitlines(keepends=True))
        self._blocks = CodeBlockMap(self._lines)
        return self

    def contains(self, import_line: str) -> bool:
        r"""Return whether a top-level line equals *import_line* net of terminator.

        The comparison strips each physical line's trailing ``\r``, ``\n``, or
        ``\r\n`` before matching, so a CRLF or lone-CR host still matches the
        terminator-free canonical string (\S2.4). A copy inside a code block is
        not top-level and never matches.
        """
        return any(
            top and self._matches(line, import_line) for line, top in self._pairs()
        )

    def without(self, import_line: str) -> str:
        r"""Return the document with every top-level *import_line* occurrence removed.

        Removes **every** top-level match, so a file that somehow carried two
        collapses to zero (\S2.4 duplicate heal). Lines that are not removed are
        rejoined byte-for-byte, and an inert copy inside a code block is left
        untouched.
        """
        return "".join(
            line
            for line, top in self._pairs()
            if not (top and self._matches(line, import_line))
        )

    def with_inserted(self, import_line: str) -> str:
        """Return the document with *import_line* inserted in sorted position.

        Inserts immediately before the first top-level tool-owned import
        (``@.punt-labs/<tool>/CLAUDE.md`` or ``@.punt-labs/local/<tool>/CLAUDE.md``)
        that sorts after *import_line* in ascending code-point order, or
        immediately after the last existing tool-owned import if none sorts
        after it, or at EOF if the document has no tool-owned imports (TED-13).
        Existing lines are never moved. Raises :class:`ValueError` when the
        document ends inside an unclosed fence (TED-14).

        Raises:
            ValueError: If the document ends inside an unterminated fence.
        """
        if self._blocks.open_at_eof:
            msg = "CLAUDE.md ends inside an unclosed code fence; close it first"
            raise ValueError(msg)
        eol = self._eol()
        tool_indices = [
            idx
            for idx, (line, top) in enumerate(self._pairs())
            if top and self._TOOL_IMPORT.fullmatch(line.rstrip("\r\n")) is not None
        ]
        if not tool_indices:
            return self.with_appended(import_line)
        lines = list(self._lines)
        for idx in tool_indices:
            if lines[idx].rstrip("\r\n") > import_line:
                lines.insert(idx, f"{import_line}{eol}")
                return "".join(lines)
        last = tool_indices[-1]
        if not lines[last].endswith(("\n", "\r")):
            lines[last] = f"{lines[last]}{eol}"
        lines.insert(last + 1, f"{import_line}{eol}")
        return "".join(lines)

    def with_appended(self, import_line: str) -> str:
        """Return the document with *import_line* appended as one bare top-level line.

        Ensures a separating terminator first, so the import is never glued to
        the user's last line, and uses the document's own EOL convention for both
        the separator and the appended line -- so the tool's line matches the
        surrounding endings and stays terminator-insensitively matchable on
        re-run. Raises :class:`ValueError` when the document ends
        inside a fence that never closes, where the line would be inert.

        Raises:
            ValueError: If the document ends inside an unterminated fence.
        """
        if self._blocks.open_at_eof:
            msg = "CLAUDE.md ends inside an unclosed code fence; close it first"
            raise ValueError(msg)
        text = "".join(self._lines)
        eol = self._eol()
        if text and not text.endswith(("\n", "\r")):
            text += eol
        return f"{text}{import_line}{eol}"

    def _eol(self) -> str:
        r"""Return the document's line-ending convention, defaulting to ``\n``.

        Each line kept by ``splitlines(keepends=True)`` carries its own
        terminator, so the first terminated line fixes the host convention;
        ``\r\n`` is tested before ``\n`` because a CRLF line ends with both.
        """
        for line in self._lines:
            if line.endswith("\r\n"):
                return "\r\n"
            if line.endswith("\n"):
                return "\n"
            if line.endswith("\r"):
                return "\r"
        return "\n"

    def _pairs(self) -> Iterator[tuple[str, bool]]:
        """Yield each physical line paired with whether it is top-level."""
        return zip(self._lines, self._blocks.top_level, strict=True)

    @staticmethod
    def _matches(line: str, import_line: str) -> bool:
        """Match a physical line against *import_line*, net of its terminator."""
        return line.rstrip("\r\n") == import_line
