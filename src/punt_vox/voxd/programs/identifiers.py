"""Identity, reference, and diagnostic value objects for the Programs domain.

Holds the Program name, the resolved ``playlist:2`` :class:`PartRef` (finding
#7), and the Z ``[REASON]`` diagnostic -- the addressing and diagnostic
vocabulary shared across the domain.
"""

from __future__ import annotations

from typing import Self

from punt_vox.voxd.programs.format import Format

__all__ = ["PartRef", "ProgramName", "Reason"]


class Reason:
    """A non-empty human-readable diagnostic (Z ``[REASON]``).

    Every inhabitant of the Z ``REASON`` type is non-empty -- "an empty reason
    is not a reason" -- so construction rejects blank text (PY-CC-2). The text
    is otherwise opaque and stored verbatim.
    """

    __slots__ = ("_text",)
    _text: str

    def __new__(cls, text: str) -> Self:
        if not text.strip():
            msg = "reason must be non-empty"
            raise ValueError(msg)
        self = super().__new__(cls)
        self._text = text
        return self

    @property
    def text(self) -> str:
        """Return the diagnostic text."""
        return self._text

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Reason):
            return NotImplemented
        return self._text == other._text

    def __hash__(self) -> int:
        return hash((Reason, self._text))

    def __repr__(self) -> str:
        return f"Reason({self._text!r})"

    def __str__(self) -> str:
        return self._text


class ProgramName:
    """The addressable identity of a Program -- its on-disk directory name.

    Non-empty and free of path separators: the name is a single directory
    component resolved identically by CLI, MCP, and daemon (PY-CC-2).
    """

    __slots__ = ("_value",)
    _value: str

    def __new__(cls, value: str) -> Self:
        if not value.strip():
            msg = "program name must be non-empty"
            raise ValueError(msg)
        if "/" in value or "\\" in value:
            msg = f"program name must not contain path separators: {value!r}"
            raise ValueError(msg)
        self = super().__new__(cls)
        self._value = value
        return self

    @property
    def value(self) -> str:
        """Return the directory-component name."""
        return self._value

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ProgramName):
            return NotImplemented
        return self._value == other._value

    def __hash__(self) -> int:
        return hash((ProgramName, self._value))

    def __repr__(self) -> str:
        return f"ProgramName({self._value!r})"

    def __str__(self) -> str:
        return self._value


class PartRef:
    """A resolved 1-based reference to a Part within a named Program (``playlist:2``).

    The ``index`` addresses the Program's Parts ordered by intrinsic index;
    resolving it to a ``Part`` -- and reporting an out-of-range index -- is a
    surface concern the CLI owns before any transition runs (finding #7), not
    modelled state.
    """

    __slots__ = ("_format", "_index")
    _format: Format
    _index: int

    def __new__(cls, fmt: Format, index: int) -> Self:
        if index < 1:
            msg = f"part reference index must be >= 1, got {index}"
            raise ValueError(msg)
        self = super().__new__(cls)
        self._format = fmt
        self._index = index
        return self

    @classmethod
    def parse(cls, token: str) -> Self:
        """Parse a ``format:index`` surface token such as ``playlist:2``."""
        head, sep, tail = token.partition(":")
        if not sep:
            msg = f"malformed part reference (want 'format:index'): {token!r}"
            raise ValueError(msg)
        try:
            fmt = Format(head)
        except ValueError as exc:
            msg = f"unknown format in part reference: {head!r}"
            raise ValueError(msg) from exc
        try:
            index = int(tail)
        except ValueError as exc:
            msg = f"part reference index is not an integer: {tail!r}"
            raise ValueError(msg) from exc
        return cls(fmt, index)

    @property
    def format(self) -> Format:
        """Return the referenced Program format."""
        return self._format

    @property
    def index(self) -> int:
        """Return the 1-based Part index."""
        return self._index

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, PartRef):
            return NotImplemented
        return self._format == other._format and self._index == other._index

    def __hash__(self) -> int:
        return hash((PartRef, self._format, self._index))

    def __repr__(self) -> str:
        return f"PartRef({self._format!s}:{self._index})"
