"""The YAML frontmatter grammar: parsing a block, and editing one in place."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Self, final

from punt_vox.types_errors import ConfigValueError

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = ["FrontmatterBlock"]

_LINE_START = r"(?:^|(?<=\r)(?!\n))"
_LINE_END = r"(?=[\r\n]|\Z)"
_NEWLINE = r"(?:\r\n|\r(?!\n)|\n)"
_INLINE_SPACE = r"[^\S\r\n]*"
# Only an indented line can continue a legacy value; its grammar stays the same.
_VALUE_CONTINUATION = (
    rf"(?:{_NEWLINE}{_INLINE_SPACE})*{_NEWLINE}[ \t]{_INLINE_SPACE}(?=\S)"
)
_FIELD_VALUE = (
    rf":{_INLINE_SPACE}(?:{_VALUE_CONTINUATION})?"
    rf'"?([^"\r\n]*)"?{_INLINE_SPACE}{_LINE_END}'
)
_FIELD_RE = re.compile(rf"{_LINE_START}([\w.-]+){_FIELD_VALUE}", re.MULTILINE)
# A fence is a `---` line; it only opens a block on the file's very first line,
# and only then does a later one close it. Without the opening anchor a
# markdown horizontal rule buried in prose reads as a closing fence, and an
# inserted field lands above that rule instead of in the frontmatter.
_FENCED_HEADER_RE = re.compile(
    rf"\A---[ \t]*(?={_NEWLINE}).*?(?P<separator>{_NEWLINE})---[ \t]*{_LINE_END}",
    re.DOTALL,
)
# Each character that ends a quoted value early, and how a rejection names it.
_UNQUOTABLE = (("\n", "newlines"), ("\r", "newlines"), ('"', "double-quotes"))


@final
class FrontmatterBlock:
    """One config file's text, and every question about its frontmatter.

    Immutable: an edit answers a new block rather than changing this one, so
    the text that gets written is always a value someone chose, never a
    half-applied edit left behind by a failure partway through a batch.

    Holding the grammar here leaves :class:`~punt_vox.frontmatter.Frontmatter`
    with the file -- where it lives, whether it exists, what to log about it --
    and neither has to know the other's job.
    """

    __slots__ = ("_parts",)

    _parts: tuple[str, str]

    def __new__(cls, text: str) -> Self:
        """Create a frontmatter block."""
        self = super().__new__(cls)
        # Without a fence pair, preserve legacy reads and in-place edits.
        # Otherwise, the tail owns the newline before the closing fence.
        fences = _FENCED_HEADER_RE.match(text)
        boundary = len(text) if fences is None else fences.start("separator")
        self._parts = text[:boundary], text[boundary:]
        return self

    @classmethod
    def rendered(cls, updates: dict[str, str]) -> Self:
        """Return a complete block holding exactly *updates* and nothing else."""
        lines = [f'{k}: "{v}"' for k, v in updates.items()]
        return cls("---\n" + "\n".join(lines) + "\n---\n")

    @staticmethod
    def validate_value(value: str) -> None:
        """Reject values that would corrupt the ``key: "<value>"`` round-trip.

        The parser reads up to the first quote or newline, so either would
        truncate the field. Apostrophes are safe, so ``I'm tired`` survives.

        Raises :class:`~punt_vox.types_errors.ConfigValueError` -- its own
        type, not a bare ``ValueError``, because a caller answering this has
        a real value it cannot store, not a malformed request to discard.

        Raises:
            ConfigValueError: If the value contains a newline or double quote.
        """
        for char, kind in _UNQUOTABLE:
            if char in value:
                msg = f"config values must not contain {kind}, got: {value!r}"
                raise ConfigValueError(msg)

    @property
    def text(self) -> str:
        """Return the block's whole text, ready to write."""
        return "".join(self._parts)

    def fields(self) -> dict[str, str]:
        """Return every non-empty frontmatter field."""
        header, _ = self._parts
        return self._parse(header)

    def field(self, name: str) -> str | None:
        """Return one frontmatter field, or ``None`` when it is absent or empty.

        ``None`` is the documented contract here, not a giving-up: a config
        field the user never set has no value to answer with, and every
        caller reads it as "unset" and falls back to its own default.
        """
        header, _ = self._parts
        return self._parse(header, self._field_re(name)).get(name)

    def accepts(self, keys: Iterable[str]) -> bool:
        """Return whether every key in *keys* has somewhere to go in this block.

        A key already present is replaced where it sits; one that is absent
        needs the closing fence to sit above. Text with neither is not
        frontmatter this can edit, and the caller rebuilds it whole rather
        than appending fields below prose where no reader would find them.
        """
        header, tail = self._parts
        return bool(tail) or all(self._field_re(key).search(header) for key in keys)

    def with_fields(self, updates: dict[str, str]) -> Self:
        """Return a block with *updates* applied, each replaced or inserted.

        Assumes :meth:`accepts` already said yes for these keys.
        """
        header, tail = self._parts
        for key, value in updates.items():
            line = f'{key}: "{value}"'
            # A replacement template expands backslashes; doubled, they stay text.
            template = line.replace("\\", r"\\")
            header, replaced = self._field_re(key).subn(template, header)
            if not replaced:
                header += tail[: tail.index("---")] + line
        return type(self)(header + tail)

    @staticmethod
    def _parse(header: str, pattern: re.Pattern[str] = _FIELD_RE) -> dict[str, str]:
        return {
            match.group(1): value
            for match in pattern.finditer(header)
            if (value := match.group(2).strip())
        }

    @staticmethod
    def _field_re(key: str) -> re.Pattern[str]:
        """Return the pattern matching *key*'s whole line, quoted or bare."""
        return re.compile(
            rf"{_LINE_START}({re.escape(key)}){_FIELD_VALUE}", re.MULTILINE
        )
