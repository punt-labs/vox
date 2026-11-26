"""Distinguish live Darwin process groups from zombie-only groups."""

from __future__ import annotations

import subprocess
from typing import final


@final
class DarwinProcessTable:
    """Only use complete, successful Darwin process listings as evidence."""

    __slots__ = ()

    @staticmethod
    def has_live_members(group: int, timeout: float) -> bool:
        """Check for a non-zombie member of the group without sending a signal."""
        scan = subprocess.run(
            ["/bin/ps", "-ax", "-o", "pgid=,stat="],
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        rows = scan.stdout.strip().splitlines()
        if scan.stderr or not rows:
            raise RuntimeError(
                "Cannot verify worker process group: incomplete ps output"
            )
        expected = str(group)
        return any(
            pgid == expected and not state.startswith("Z")
            for pgid, state in map(str.split, rows)
        )
