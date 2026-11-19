"""Read CLI JSON segment files and report input failures as usage errors."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Self, cast

import typer


class SegmentFile:
    """Own the decoding and validation of a --from JSON segments file."""

    __slots__ = ("_path",)

    _path: Path

    def __new__(cls, path: Path) -> Self:
        self = super().__new__(cls)
        self._path = path
        return self

    def segments(self) -> list[str]:
        """Parse a JSON segments file into a list of text strings."""
        raw = self._read_json()
        if not isinstance(raw, list):
            raise typer.BadParameter("--from file must contain a JSON array.")
        items = cast("list[object]", raw)
        return [
            text
            for i, item in enumerate(items)
            if (text := self._segment_text(i, item))
        ]

    def _read_json(self) -> object:
        """Read UTF-8 JSON, translating malformed input into CLI usage errors."""
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise typer.BadParameter(
                f"Cannot read --from file: {exc.strerror or str(exc)}."
            ) from exc
        except UnicodeDecodeError as exc:
            raise typer.BadParameter("--from file must contain valid UTF-8.") from exc
        except json.JSONDecodeError as exc:
            raise typer.BadParameter("--from file must contain valid JSON.") from exc

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
