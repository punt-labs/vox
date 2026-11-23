"""Distinguish confirmed tmux session loss from unavailable status."""

from __future__ import annotations

from typing import TYPE_CHECKING, final

if TYPE_CHECKING:
    from subprocess import CompletedProcess


@final
class TmuxProbe:
    """Classify checked tmux liveness responses."""

    __slots__ = ()

    @staticmethod
    def missing(result: CompletedProcess[str]) -> bool:
        """Identify an absent session or absent local server socket."""
        if result.returncode == 0:
            return False
        error = result.stderr.strip()
        if error.startswith(
            ("can't find session:", "no such session", "no server running on ")
        ):
            return True
        if error.startswith("error connecting to ") and error.endswith(
            "(No such file or directory)"
        ):
            return True
        raise RuntimeError("Worker tmux liveness probe failed")

    @classmethod
    def pane_alive(cls, result: CompletedProcess[str]) -> bool:
        """Return liveness only when tmux reports a definite pane state."""
        if cls.missing(result):
            return False
        match result.stdout.strip():
            case "0":
                return True
            case "1":
                return False
            case _:
                raise RuntimeError("Worker tmux liveness probe returned no pane state")
