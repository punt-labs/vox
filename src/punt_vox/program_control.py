"""The request and result value objects the surfaces exchange with the daemon.

The vocabulary of the :class:`~punt_vox.program_gateway.ProgramGateway` seam: the
authoring request (:class:`StartRequest`), the replay request
(:class:`SelectionRequest`), the applied/rejected result
(:class:`CommandOutcome`), and a catalogue entry (:class:`ProgramSummary`). Plain
value objects with no I/O, so the CLI, the MCP tools, and their test fakes share
the types without importing the daemon.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Self, final

from punt_vox.music_prompts import PromptSet

__all__ = ["CommandOutcome", "ProgramSummary", "SelectionRequest", "StartRequest"]


@final
@dataclass(frozen=True, slots=True)
class StartRequest:
    """The authoring input for turning a Program on (the ``music on`` command).

    All fields optional: ``style`` persists across calls, ``vibe`` is the session
    mood recorded as the album's vibe tag, ``name`` binds a curated
    album, ``prompts`` carries the agent base + per-slot variations (``None`` => a
    minimal literal fallback -- absence is the contract, PY-TS-14).
    """

    style: str | None = None
    vibe: str | None = None  # session mood; None falls back to the style tag
    name: str | None = None
    prompts: PromptSet | None = None


@final
@dataclass(frozen=True, slots=True)
class SelectionRequest:
    """The replay input for playing a Selection (the ``music play`` command).

    ``id`` is a *direct-lookup* axis served by ``catalog.by_id`` -- distinct from
    the ``style``/``vibe``/``name`` tag axes that build a tag query; it is
    never folded into the tag filter. All fields optional: an all-``None`` request
    replays every album (the cross-genre radio).
    """

    style: str | None = None
    vibe: str | None = None
    name: str | None = None
    id: str | None = None  # direct album-id lookup, never a tag axis


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
    """One catalog album as a ``list`` renders it: id, tags, and part counts."""

    id: str
    style: str
    vibe: str
    format: str  # the human surface label -- "music" for a playlist
    ready: int  # playable Parts
    total: int = 0  # all Parts incl. failed; defaults to ready when unspecified
    name: str | None = None  # the curated handle, or None for a tag-addressed album

    def display_line(self) -> str:
        """Return a human-readable one-line summary of the album."""
        parts = max(self.total, self.ready)  # total counts ready+failed, or 0 unset
        handle = self.name or f"{self.style}--{self.vibe}"
        return f"{handle} [{self.id}] — {self.ready}/{parts} part(s) [{self.format}]"
