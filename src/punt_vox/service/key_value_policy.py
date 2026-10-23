"""Decide whether a ``keys.env`` value may be written, and say why not."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Self, final

# Every character ``str.splitlines`` ends a line on. Any of them inside a value
# would let the value smuggle a second ``key=value`` line into keys.env.
_LINE_BREAKS = frozenset("\n\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029")
# HTTP header content: printable ASCII with no space, 0x21-0x7E.
_HEADER_SAFE = frozenset(map(chr, range(0x21, 0x7F)))


@final
@dataclass(frozen=True, slots=True)
class Refusal:
    """Why a value was refused: the class and position of its first bad character.

    The character itself is never kept, so a refusal is safe to log and print
    even when the value is a secret.
    """

    char_class: str
    position: int  # 1-based, counted in the whitespace-trimmed value
    rule: str

    @classmethod
    def at(cls, value: str, index: int, rule: str) -> Self:
        """Return the refusal for the character at *index* of *value*."""
        return cls(cls._classify(value[index]), index + 1, rule)

    @staticmethod
    def _classify(char: str) -> str:
        """Name the class *char* belongs to, without naming *char*."""
        if char == " ":
            return "a space"
        if char in _LINE_BREAKS:
            return "a line break"
        if not char.isprintable():
            return "a non-printable character"
        return "a non-ASCII character"

    def __str__(self) -> str:
        return f"{self.char_class} at position {self.position} ({self.rule})"


@final
class ValuePolicy:
    """The characters one ``keys.env`` value may hold."""

    __slots__ = ("_allowed", "_rule")

    _allowed: Callable[[str], bool]
    _rule: str

    def __new__(cls, allowed: Callable[[str], bool], rule: str) -> Self:
        self = super().__new__(cls)
        self._allowed = allowed
        self._rule = rule
        return self

    @classmethod
    def header_safe(cls) -> Self:
        """Return the policy for a value sent as HTTP header content."""
        return cls(_HEADER_SAFE.__contains__, "no HTTP header may carry it")

    @classmethod
    def printable(cls) -> Self:
        """Return the policy for a value that never goes in a header.

        ``str.isprintable`` is false for every line break ``str.splitlines``
        honours, so a printable value cannot end its own line.
        """
        return cls(str.isprintable, "it could start a new keys.env line")

    def refusal(self, value: str) -> Refusal | None:
        """Return why *value* is refused, or ``None`` when it may be written.

        ``None`` is the accepting answer, not a failure: most values pass.
        """
        bad = (i for i, char in enumerate(value) if not self._allowed(char))
        index = next(bad, None)  # None: every character is allowed
        return None if index is None else Refusal.at(value, index, self._rule)
