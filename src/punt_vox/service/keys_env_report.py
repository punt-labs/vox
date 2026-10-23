"""What ``vox daemon install`` did not take from the environment, and why."""

from __future__ import annotations

from dataclasses import dataclass
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
