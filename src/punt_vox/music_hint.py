"""The read-only music directive a vibe change returns while music is playing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Self, final

from punt_vox.types_programs.mode import PlaybackStatus
from punt_vox.types_programs.status import ProgramStatus

__all__ = ["MusicHint"]

_UNKNOWN_MOOD = "the current session mood"


@final
@dataclass(frozen=True, slots=True)
class MusicHint:
    """An imperative directive telling the agent to re-pool music to a new mood.

    The ``vibe`` tool returns this (read-only) whenever a Program is playing so the
    agent authors fresh ``(mood x style)`` prompts and calls the ``music`` tool
    itself. The ``vibe`` tool never drives playback -- the hint is the whole
    coupling, and it is soft: the directive is phrased imperatively so the agent
    reliably acts on it, the same device the Stop hook uses to make a soft path
    reliable.
    """

    _mood: str
    _style: str | None

    @classmethod
    def for_status(
        cls, status: ProgramStatus, mood: str | None, style: str | None
    ) -> Self | None:
        """Return a hint only when *status* is genuinely audible, else ``None``.

        A hint fires solely for the two playing modes. Off, generating, retrying,
        and failed are NOT playing -- a re-pool directive would be a lie there, so
        they return ``None`` (the documented "nothing to re-pool" contract). A
        cleared mood (``/vibe auto``/``off``) still hints while a Program plays.
        """
        if status.mode.status is not PlaybackStatus.PLAYING:
            return None
        return cls(_mood=mood or _UNKNOWN_MOOD, _style=style)

    @property
    def style(self) -> str | None:
        """Return the style being re-pooled, or ``None`` when it is unknown."""
        return self._style

    @property
    def directive(self) -> str:
        """Return the imperative re-pool instruction, naming the current style."""
        if self._style is None:
            call = (
                'music(mode="on", base_prompt=..., '
                "variations=[<12 genre-mood prompts>])"
            )
            return (
                f"Music is playing. Author 12 rich prompts for {self._mood} in the "
                f"current genre and call {call}. Do it now."
            )
        call = (
            f'music(mode="on", style="{self._style}", base_prompt=..., '
            "variations=[<12 genre-mood prompts>])"
        )
        return (
            f"Music is playing (style={self._style}). Author 12 rich "
            f"{self._style} x {self._mood} prompts and call {call}. Do it now."
        )

    def music_state(self) -> dict[str, object]:
        """Return the observable music state the reply nests under ``music``."""
        return {"playing": True, "style": self._style}
