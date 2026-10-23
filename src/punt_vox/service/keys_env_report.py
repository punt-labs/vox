"""What ``vox daemon install`` did not take from the environment, and why."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import final


@final
@dataclass(frozen=True, slots=True)
class KeyNotice:
    """One provider key the writer skipped, by name and reason only.

    Never carries the value: a notice is logged and printed to the operator.
    """

    key: str
    reason: str
    kept: bool  # whether a stored value for the key survived the write

    def __str__(self) -> str:
        outcome = (
            "kept the stored value"
            if self.kept
            else "nothing stored, so its provider has no credential"
        )
        return f"{self.key}: {self.reason}; {outcome}"


@final
@dataclass(frozen=True, slots=True)
class KeysEnvReport:
    """Where one ``keys.env`` write went and every key it did not take."""

    path: Path
    notices: tuple[KeyNotice, ...]

    def notice_lines(self) -> tuple[str, ...]:
        """Return one indented status line per notice, for the install output."""
        return tuple(f"    ! {notice}" for notice in self.notices)
