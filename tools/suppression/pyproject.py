"""Count ``[tool.ruff.lint.per-file-ignores]`` rule codes in pyproject.toml."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Self


class PerFileIgnoresCounter:
    """Count the rule codes waived under ruff's per-file-ignores table."""

    _total: int
    _breakdown: dict[str, int]

    def __new__(cls, pyproject_path: Path) -> Self:
        self = super().__new__(cls)
        self._total = 0
        self._breakdown = {}
        self._parse(pyproject_path)
        return self

    @property
    def total(self) -> int:
        """Return the total number of per-file-ignore rule codes."""
        return self._total

    @property
    def breakdown(self) -> dict[str, int]:
        """Return the code count per glob pattern."""
        return dict(self._breakdown)

    def _parse(self, pyproject_path: Path) -> None:
        if not pyproject_path.exists():
            return
        try:
            data = tomllib.loads(pyproject_path.read_text())
        except (tomllib.TOMLDecodeError, OSError):
            return
        ignores = (
            data.get("tool", {})
            .get("ruff", {})
            .get("lint", {})
            .get("per-file-ignores", {})
        )
        if not isinstance(ignores, dict):
            return
        for pattern, codes in ignores.items():
            if isinstance(codes, list):
                self._breakdown[pattern] = len(codes)
                self._total += len(codes)
