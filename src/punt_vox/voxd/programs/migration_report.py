"""The outcome of a one-time legacy ``tracks/`` -> ``programs/`` migration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import final

__all__ = ["MigrationReport"]


@final
@dataclass(frozen=True, slots=True)
class MigrationReport:
    """What a migration run moved: the Programs created and Parts relocated."""

    names: tuple[str, ...]
    parts: int

    @property
    def programs(self) -> int:
        """Return the number of Programs the run created."""
        return len(self.names)

    @property
    def is_empty(self) -> bool:
        """Return whether the run had nothing to migrate."""
        return not self.names

    def summary(self) -> str:
        """Return a human-readable one-line summary for the CLI."""
        if self.is_empty:
            return "nothing to migrate"
        listed = ", ".join(self.names)
        return (
            f"migrated {self.parts} track(s) into {self.programs} program(s): {listed}"
        )
