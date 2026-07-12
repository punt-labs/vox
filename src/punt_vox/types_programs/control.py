"""The request and result value objects the surfaces exchange with the daemon.

The ProgramGateway vocabulary -- the ``StartRequest``/``SelectionRequest`` inputs,
the applied/rejected :class:`CommandOutcome`, and the :class:`ProgramSummary`
catalogue entry -- plain value objects with no I/O, shared by CLI, MCP, and fakes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Self, final

from punt_vox.types_programs.prompts import PromptSet

__all__ = ["CommandOutcome", "ProgramSummary", "SelectionRequest", "StartRequest"]


@final
@dataclass(frozen=True, slots=True)
class StartRequest:
    """The authoring input for turning a Program on (the ``music on`` command).

    All fields optional: ``style`` persists, ``vibe`` is the session mood tag,
    ``name`` binds a curated album, ``prompts`` carries the agent base + per-slot
    variations (``None`` => a minimal literal fallback -- absence is the contract).
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
    """Whether the daemon applied a command, and a line describing the result.

    The live protocol acks at *enqueue* with a bare reply (no ``applied`` flag),
    so a command always parses to ``applied=True`` with an empty ``message``; the
    rejected ``applied=False`` path is caught-and-logged in the daemon's async
    apply and not yet sent. The flag and forward-looking parse are kept so
    surfacing rejection later stays a daemon-side change, not a client-contract
    change.
    """

    applied: bool
    message: str

    @classmethod
    def ok(cls, message: str) -> Self:
        """Return an applied outcome carrying ``message``."""
        return cls(applied=True, message=message)

    @classmethod
    def rejected(cls, message: str) -> Self:
        """Return a rejected outcome (forward-looking; unused by the live daemon)."""
        return cls(applied=False, message=message)

    def display(self, applied_default: str) -> str:
        """Return the daemon's reason, or ``applied_default`` when it is silent."""
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
