"""The read-only music directive a vibe change returns while music is playing."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Self, final

from punt_vox.types_programs.status import ProgramStatus

__all__ = ["MusicHint"]

_UNKNOWN_MOOD = "the current session mood"


@final
@dataclass(frozen=True, slots=True)
class MusicHint:
    """An imperative directive telling the agent to re-pool music to a new mood.

    The ``vibe`` tool returns this (read-only) whenever a Program is playing *and*
    its genre is known, so the agent authors fresh ``(mood x style)`` prompts and
    calls the ``music`` tool itself. The ``vibe`` tool never drives playback -- the
    hint is the whole coupling, and it is soft: the directive is phrased
    imperatively so the agent reliably acts on it, the same device the Stop hook
    uses to make a soft path reliable. A re-pool changes the mood but *preserves
    the genre*, so an unknown style yields no hint (see :meth:`for_status`).
    """

    # A cleared session mood (/vibe auto|off) has no value -- absence is the
    # documented state, so directive substitutes a neutral placeholder (PY-TS-14).
    _mood: str | None
    _style: str

    @classmethod
    def for_status(
        cls, status: ProgramStatus, mood: str | None, style: str | None
    ) -> Self | None:
        """Return a hint only when *status* is audible and *style* is known.

        A hint fires solely for the two playing modes with a known genre. Off,
        generating, retrying, and failed are NOT playing -- a re-pool directive
        would be a lie there. An unknown *style* (``None``) also returns ``None``:
        with no genre to name, a re-pool cannot preserve it, and following the
        hint would silently switch to the default genre. Both are the documented
        "nothing to re-pool" contract. A cleared mood (``/vibe auto``/``off``)
        still hints while a Program plays with a known style.
        """
        if not status.is_playing or style is None:
            return None
        return cls(_mood=mood, _style=style)

    @property
    def style(self) -> str:
        """Return the known style being re-pooled."""
        return self._style

    @property
    def directive(self) -> str:
        """Return the imperative re-pool instruction, naming the current style."""
        mood = self._mood or _UNKNOWN_MOOD
        # json.dumps escapes the style into a valid literal the example can't break.
        return (
            f"Music is playing (style={self._style}). Author 12 rich "
            f"{self._style} x {mood} prompts and call "
            f'music(mode="on", style={json.dumps(self._style)}, base_prompt=..., '
            "variations=[<12 genre-mood prompts>]). Do it now."
        )

    def music_state(self) -> dict[str, object]:
        """Return the observable music state the reply nests under ``music``."""
        return {"playing": True, "style": self._style}
