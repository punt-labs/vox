"""The YAML frontmatter grammar: parsing a block, and editing one in place."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Self, final

from punt_vox.types_errors import ConfigValueError

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = ["FrontmatterBlock"]

_FIELD_RE = re.compile(r'^([a-z_]+):\s*"?([^"\n]*)"?\s*$', re.MULTILINE)
# A fence is a `---` line; it only opens a block on the file's very first line,
# and only then does a later one close it. Without the opening anchor a
# markdown horizontal rule buried in prose reads as a closing fence, and an
# inserted field lands above that rule instead of in the frontmatter.
_FENCED_HEADER_RE = re.compile(
    r"\A---[ \t]*\n.*?(?P<closing>^---[ \t]*$)", re.MULTILINE | re.DOTALL
)


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
        self = super().__new__(cls)
        # Without a fence pair, preserve legacy reads and in-place edits.
        # Otherwise, the tail owns the newline before the closing fence.
        fences = _FENCED_HEADER_RE.match(text)
        boundary = len(text) if fences is None else fences.start("closing") - 1
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
        """
        if "\n" in value or "\r" in value:
            msg = f"config values must not contain newlines, got: {value!r}"
            raise ConfigValueError(msg)
        if '"' in value:
            msg = f"config values must not contain double-quotes, got: {value!r}"
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
        return self._parse(header).get(name)

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
            field_re = self._field_re(key)
            if field_re.search(header):
                header = field_re.sub(line, header)
            elif tail:
                header += f"\n{line}"
            else:
                msg = f"no closing fence to insert {line!r} above"
                raise ValueError(msg)
        return type(self)(header + tail)

    @staticmethod
    def _parse(header: str) -> dict[str, str]:
        found: dict[str, str] = {}
        for match in _FIELD_RE.finditer(header):
            val = match.group(2).strip()
            if val:
                found[match.group(1)] = val
        return found

    @staticmethod
    def _field_re(key: str) -> re.Pattern[str]:
        """Return the pattern matching *key*'s whole line, quoted or bare."""
        return re.compile(rf"^{re.escape(key)}:\s*\"?([^\"\n]*)\"?\s*$", re.MULTILINE)
