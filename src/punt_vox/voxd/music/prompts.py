"""The generation prompts one music pool draws from -- agent-authored or fallback.

Genre understanding lives in the calling agent (an LLM), never in vox. The agent
authors a base prompt plus one literal, genre-accurate variation per pool slot
and hands them to voxd; vox composes ``base + variation[i]`` for track ``i`` and
pipes the string to the provider. When no agent is in the loop (a hook-driven
vibe change), the pool falls back to a minimal literal prompt that names the
genre and mood and nothing else -- no "background music for deep work" boilerplate,
which homogenized every genre into the same mellow bed.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Self

from punt_vox.voxd.music.pool import POOL_SIZE

__all__ = ["PromptSet"]


@dataclass(frozen=True, slots=True)
class PromptSet:
    """A pool's generation prompts: an agent base plus per-track variations.

    ``variations`` is empty for a fallback set (every track uses ``base``) and
    holds ``POOL_SIZE`` entries for an agent set (track ``i`` uses variation
    ``i``). Frozen and hashable, so it can key a :class:`FillTarget`: swapping in
    fresh agent prompts changes the target and restarts the background fill.
    """

    base: str
    variations: tuple[str, ...]

    @classmethod
    def from_agent(cls, base: str, variations: Sequence[str]) -> Self:
        """Build a validated agent prompt set: a base plus ``POOL_SIZE`` variations.

        Raise ``ValueError`` on any shape the agent got wrong -- an empty base, a
        wrong variation count, or a blank variation -- so the tool surfaces a
        clear message rather than silently generating a degenerate pool.
        """
        clean_base = base.strip()
        if not clean_base:
            msg = "base_prompt must be a non-empty string"
            raise ValueError(msg)
        cleaned = tuple(v.strip() for v in variations)
        if len(cleaned) != POOL_SIZE:
            msg = f"variations must have exactly {POOL_SIZE} entries, got {len(cleaned)}"
            raise ValueError(msg)
        if any(not v for v in cleaned):
            msg = "every variation must be a non-empty string"
            raise ValueError(msg)
        return cls(base=clean_base, variations=cleaned)

    @classmethod
    def fallback(cls, style: str, mood: str) -> Self:
        """Build the minimal literal fallback for a pool with no agent prompts.

        Shape: ``"<style> music, <mood>. instrumental, loopable."`` -- genre
        first, no generic deep-work boilerplate. An empty style becomes
        ``ambient``; an empty mood is dropped.
        """
        genre = f"{style.strip()} music" if style.strip() else "ambient music"
        head = f"{genre}, {mood.strip()}" if mood.strip() else genre
        return cls(base=f"{head}. instrumental, loopable.", variations=())

    def prompt_for(self, index: int) -> str:
        """Return the final prompt for track ``index``.

        A fallback set (no variations) returns ``base`` for every track. An agent
        set composes ``base`` with the ``index``-th variation so the pool spans
        the agent's genre-accurate descriptions.
        """
        if not self.variations:
            return self.base
        return f"{self.base} {self.variations[index % len(self.variations)]}"
