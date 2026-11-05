"""Who a process is: its start time and command line, as ``ps`` reports them.

A pid alone names whichever process the kernel last gave that number to. The
pair (start time, command line) names one process: a reused pid has a later
start time, so a handle that re-reads the pair and finds it changed knows the
process it identified has exited.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import ClassVar, Self, final

__all__ = ["ProcessIdentity"]


@final
@dataclass(frozen=True, slots=True)
class ProcessIdentity:
    """One process's start time and command line."""

    started: str
    command: str

    # ``lstart`` under LC_ALL=C is fixed width: "Tue Nov  4 15:53:01 2026".
    _LSTART_WIDTH: ClassVar[int] = 24
    # Loose on purpose: it only ever runs on processes ``fuser``/``lsof``
    # reported on the daemon port, never on the whole process table.
    _VOXD: ClassVar[re.Pattern[str]] = re.compile(r"\bvoxd\b")

    @classmethod
    def from_ps(cls, line: str) -> Self:
        """Parse one ``ps -o lstart= -o command=`` line; raise if malformed."""
        started = line[: cls._LSTART_WIDTH].strip()
        command = line[cls._LSTART_WIDTH :].strip()
        if not started or not command:
            msg = f"unparseable ps line: {line!r}"
            raise ValueError(msg)
        return cls(started=started, command=command)

    def is_voxd(self) -> bool:
        """Return True when the command line runs voxd."""
        return bool(self._VOXD.search(self.command))
