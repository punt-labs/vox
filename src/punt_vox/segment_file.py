"""Read CLI JSON segment files and report input failures as usage errors."""

from __future__ import annotations

import json
from itertools import starmap
from pathlib import Path
from typing import Self, cast

import typer

from punt_vox.option_file import OptionFile


class SegmentFile:
    """Own the decoding and validation of a --from JSON segments file."""

    __slots__ = ("_path",)

    _path: Path

    def __new__(cls, path: Path) -> Self:
        """Create a reader for a JSON segments file."""
        self = super().__new__(cls)
        self._path = path
        return self

    def segments(self) -> list[str]:
        """Parse a JSON segments file into its non-empty text strings."""
        texts = starmap(self._segment_text, enumerate(self._items()))
        if segments := list(filter(bool, texts)):
            return segments
        raise typer.BadParameter("--from file has no text to speak.")

    def _items(self) -> list[object]:
        """Read the UTF-8 JSON array, translating bad input into usage errors."""
        try:
            raw = json.loads(OptionFile(self._path, "--from").text())
        except json.JSONDecodeError as exc:
            raise typer.BadParameter("--from file must contain valid JSON.") from exc
        if not isinstance(raw, list):
            raise typer.BadParameter("--from file must contain a JSON array.")
        return cast("list[object]", raw)

    @staticmethod
    def _segment_text(index: int, item: object) -> str:
        """Resolve a segment string or object, preserving its index on error."""
        match item:
            case (str() as text) | {"text": str() as text}:
                return text
            case _:
                raise typer.BadParameter(
                    f"Element {index} must be a string or an object with string text."
                )
