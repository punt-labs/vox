"""Authoritative resolution of a vibe change into config field updates."""

from __future__ import annotations

from dataclasses import dataclass

VALID_VIBE_MODES: frozenset[str] = frozenset({"auto", "manual", "off"})

__all__ = ["VALID_VIBE_MODES", "VibeChange"]


@dataclass(frozen=True, slots=True)
class VibeChange:
    """A requested vibe change: an optional mood, tags, and detection mode.

    Resolving a change yields the authoritative config field updates.  The
    rules make ``/vibe`` the single source of truth for the vibe cluster:

    - ``auto`` / ``off`` reset the whole cluster -- mood, tags, and any
      accumulated signals are cleared so nothing stale (a mood the user
      meant to drop, tags from a prior manual vibe) survives the transition
      (vox-73m5).
    - ``manual`` (or no mode) records whichever of mood/tags was supplied.
    - Setting tags always clears signals, since tags are the resolved form
      of signals.
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
            updates.update(vibe="", vibe_tags="", vibe_signals="")
        else:
            if self.mood is not None:
                updates["vibe"] = self.mood
            if self.tags is not None:
                updates["vibe_tags"] = self.tags
                updates["vibe_signals"] = ""
        if self.mode is not None:
            updates["vibe_mode"] = self.mode
        return updates
