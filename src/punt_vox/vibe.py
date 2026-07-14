"""Authoritative resolution of a vibe change into config field updates."""

from __future__ import annotations

from dataclasses import dataclass

VALID_VIBE_MODES: frozenset[str] = frozenset({"auto", "manual", "off"})

__all__ = ["VALID_VIBE_MODES", "VibeChange"]


@dataclass(frozen=True, slots=True)
class VibeChange:
    """A requested vibe change: an optional mood, tags, and detection mode.

    Resolving yields the authoritative config updates -- ``/vibe`` is the
    single source of truth for the vibe cluster:

    - ``auto`` / ``off`` clear the whole cluster so nothing stale survives.
    - A manual mood clears stale tags unless tags are supplied alongside it:
      a mood alone resets ``vibe_tags`` to empty; a mood with tags records
      them.  Tags without a mood update ``vibe_tags`` alone.
    - Any mode change resets the nudge cadence counter.
    """

    mood: str | None
    tags: str | None
    mode: str | None

    def validate(self) -> None:
        """Raise ``ValueError`` when the mode is not a recognized value."""
        if self.mode is not None and self.mode not in VALID_VIBE_MODES:
            msg = f"invalid vibe mode: {self.mode!r}"
            raise ValueError(msg)

    def resolve(self) -> dict[str, str]:
        """Return the config field updates for this change (empty if none)."""
        self.validate()
        updates: dict[str, str] = {}
        if self.mode in ("auto", "off"):
            updates.update(vibe="", vibe_tags="")
        elif self.mood is not None:
            updates["vibe"] = self.mood
            updates["vibe_tags"] = self.tags if self.tags is not None else ""
        elif self.tags is not None:
            updates["vibe_tags"] = self.tags
        if self.mode is not None:
            updates["vibe_mode"] = self.mode
            updates["vibe_nudge_turns"] = "0"
        return updates
