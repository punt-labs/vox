"""The request and result value objects the surfaces exchange with the daemon.

The vocabulary of the :class:`~punt_vox.program_gateway.ProgramGateway` seam:
the authoring request (:class:`StartRequest`), the applied/rejected result
(:class:`CommandOutcome`, F7), and a catalogue entry (:class:`ProgramSummary`).
Plain value objects with no I/O, so the CLI, the MCP tools, and their test fakes
share the types without importing the daemon.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Self, final

from punt_vox.music_prompts import PromptSet

__all__ = ["CommandOutcome", "ProgramSummary", "StartRequest"]


@final
@dataclass(frozen=True, slots=True)
class StartRequest:
    """The authoring input for turning a Program on (the ``music on`` command).

    All fields optional: ``style`` persists across calls, ``name`` replays or
    saves a track, ``prompts`` carries the agent base + per-slot variations
    (``None`` => a minimal literal fallback -- absence is the contract, PY-TS-14).
    """

    style: str | None = None
    name: str | None = None
    prompts: PromptSet | None = None


@final
@dataclass(frozen=True, slots=True)
class CommandOutcome:
    """Whether the daemon applied a command, and a line about it (design F7).

    A command whose Z precondition no longer held (a lost race -- ``next`` just
    after ``off``) is *rejected*, not an error; the caller observes which.
    """

    applied: bool
    message: str

    @classmethod
    def ok(cls, message: str) -> Self:
        """Return an applied outcome carrying ``message``."""
        return cls(applied=True, message=message)

    @classmethod
    def rejected(cls, message: str) -> Self:
        """Return a rejected (lost-race) outcome carrying ``message``."""
        return cls(applied=False, message=message)

    def display(self, applied_default: str) -> str:
        """Return the daemon's reason, or ``applied_default`` when silent (F4/F7)."""
        return self.message or applied_default


@final
@dataclass(frozen=True, slots=True)
class ProgramSummary:
    """One saved Program as a ``list`` renders it: name, format, and part counts."""

    name: str
    format: str  # the human surface label -- "music" for a playlist
    ready: int  # playable Parts
    total: int = 0  # all Parts incl. failed; defaults to ready when unspecified

    def display_line(self) -> str:
        """Return a human-readable one-line summary grouped under its Program."""
        parts = max(self.total, self.ready)  # total counts ready+failed, or 0 unset
        return f"{self.name} — {self.ready}/{parts} part(s) [{self.format}]"
