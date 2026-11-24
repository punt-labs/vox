"""The private trace file one voice call writes, reserved by its call ID."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Self

from punt_vox.voxd.voice_agent.trace import EventTrace

if TYPE_CHECKING:
    from pathlib import Path
    from typing import TextIO
    from uuid import UUID

__all__ = ["CallTrace"]


@dataclass(frozen=True, slots=True)
class CallTrace:
    """Resources for a call trace, reserved by its unique call ID."""

    path: Path
    stream: TextIO
    events: EventTrace

    @classmethod
    def reserve(cls, directory: Path, call_id: UUID) -> Self:
        """Create a private trace and refuse an ID that was already used."""
        path = directory / f"call-{call_id.hex}.jsonl"
        try:
            stream = os.fdopen(
                os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w"
            )
        except FileExistsError:
            raise ValueError("call_id has already been used") from None
        return cls(path, stream, EventTrace(stream))

    def release(self) -> None:
        """Undo a reservation no call used: close the stream, delete the file.

        The ID is free again, so the caller may retry with it.
        """
        self.stream.close()
        self.path.unlink()
