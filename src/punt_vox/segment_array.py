"""Decode the JSON array of a CLI ``--from`` segments file into its elements."""

from __future__ import annotations

import json
from itertools import starmap
from typing import TYPE_CHECKING, Self, cast, final

import typer

from punt_vox.option_file import OptionFile

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["SegmentArray", "SegmentItem"]


@final
class SegmentArray:
    """The top-level JSON array of a ``--from`` file.

    Every failure is a ``typer.BadParameter``, so the CLI rejects the whole
    file as a usage error before any segment is spoken.
    """

    __slots__ = ("_path",)

    _path: Path

    def __new__(cls, path: Path) -> Self:
        """Create a decoder for the JSON segments file at *path*."""
        self = super().__new__(cls)
        self._path = path
        return self

    def items(self) -> starmap[SegmentItem]:
        """Read the UTF-8 JSON array, translating bad input into usage errors."""
        try:
            raw = json.loads(OptionFile(self._path, "--from").text())
        except json.JSONDecodeError as exc:
            detail = f"{exc.msg}: line {exc.lineno} column {exc.colno}"
            raise typer.BadParameter(f"{self._path}: invalid JSON: {detail}") from exc
        if not isinstance(raw, list):
            raise typer.BadParameter("--from file must contain a JSON array.")
        return starmap(SegmentItem, enumerate(cast("list[object]", raw)))


@final
class SegmentItem:
    """One decoded element of a ``--from`` segments array, named by its index."""

    __slots__ = ("_index", "_item")

    _index: int
    _item: object

    def __new__(cls, index: int, item: object) -> Self:
        """Wrap the decoded JSON value at *index* of the segments array."""
        self = super().__new__(cls)
        self._index = index
        self._item = item
        return self

    def text(self) -> str:
        """Return the element's speech text: a string or an object's ``text``.

        Raises:
            typer.BadParameter: If the element has no string text, or its text
                holds a lone surrogate escape that UTF-8 cannot encode.
        """
        match self._item:
            case (str() as text) | {"text": str() as text}:
                return self._encodable(text)
            case _:
                index = self._index
                msg = f"Element {index} must be a string or an object with string text."
                raise typer.BadParameter(msg)

    def _encodable(self, text: str) -> str:
        """Return *text*, rejecting a lone surrogate that UTF-8 cannot encode."""
        try:
            text.encode("utf-8")
        except UnicodeEncodeError as exc:
            msg = (
                f"Element {self._index} text is not valid UTF-8: unpaired "
                f"surrogate U+{ord(text[exc.start]):04X} at character {exc.start}."
            )
            raise typer.BadParameter(msg) from exc
        return text
