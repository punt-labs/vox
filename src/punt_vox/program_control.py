"""The request and result value objects the surfaces exchange with the daemon.

These are the vocabulary of the :class:`~punt_vox.program_gateway.ProgramGateway`
seam: what a client asks for (:class:`StartRequest`), what it learns happened
(:class:`CommandOutcome` -- the per-command applied/rejected result, design F7),
and the catalogue entry a ``list`` renders (:class:`ProgramSummary`). They are
plain value objects with no I/O, so both the CLI and the MCP tools -- and their
in-memory test fakes -- speak the same types without importing the daemon.
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

    Every field is optional: ``style`` is a display modifier that persists across
    calls, ``name`` replays or saves a named track, and ``prompts`` carries the
    agent-authored base + per-slot variations. ``prompts is None`` means no agent
    is in the loop, so the pool falls back to a minimal literal prompt -- absence
    is the documented contract (PY-TS-14), not a missing value.
    """

    style: str | None = None
    name: str | None = None
    prompts: PromptSet | None = None


@final
@dataclass(frozen=True, slots=True)
class CommandOutcome:
    """Whether the daemon applied a command, and a human-readable line about it.

    The ``applied`` flag is design F7: a command whose Z precondition no longer
    held (a lost race -- e.g. ``next`` arriving just after ``off``) is *rejected*,
    not an error. The caller observes which happened rather than reading a log.
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
        """Return the line to show for this outcome (findings F4/F7).

        Prefer the daemon's own ``message``: on a rejected command it is the
        reason the command was refused, and returning a canned success line
        would hide it. Fall back to the caller's ``applied_default`` only when
        the command applied and the daemon reported no message of its own; a
        rejection with no message is a defensive ``"command rejected"``.
        """
        if self.message:
            return self.message
        return applied_default if self.applied else "command rejected"

    def to_dict(self) -> dict[str, object]:
        """Return the JSON object form for an MCP tool return."""
        return {"applied": self.applied, "message": self.message}


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
        parts = self.total or self.ready
        return f"{self.name} — {self.ready}/{parts} part(s) [{self.format}]"
