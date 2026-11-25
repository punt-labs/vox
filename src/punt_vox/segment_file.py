"""Read CLI JSON segment files and report input failures as usage errors."""

from __future__ import annotations

from pathlib import Path
from typing import Self

import typer

from punt_vox.segment_array import SegmentArray


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
        """Parse a JSON segments file into its non-blank text strings.

        Every element is validated before any is returned, so one bad element
        rejects the whole file.
        """
        texts = (item.text() for item in SegmentArray(self._path).items())
        if segments := list(filter(str.strip, texts)):
            return segments
        raise typer.BadParameter("--from file has no text to speak.")
