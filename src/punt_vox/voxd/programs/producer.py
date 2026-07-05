"""The per-format generation seam: author one spec into one ready Part.

``Producer`` is the boundary between the format-general fill orchestration and
the format-specific generation call (ElevenLabs music, later dialogue and
narration). It raises one of two error types so the fill loop routes cleanly to
the right Program transition: a permanent error (bad prompt / ToS / missing key)
becomes ``ProducerBadInputError``; a transient one (429 / quota / 5xx / timeout)
becomes ``ProducerTransientError`` (findings #4/#5).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, final

from punt_vox.voxd.programs.part import Part

__all__ = ["PartSpec", "Producer", "ProducerBadInputError", "ProducerTransientError"]


class ProducerBadInputError(Exception):
    """A permanent generation error -- retrying will not help."""


class ProducerTransientError(Exception):
    """A transient generation error -- a later retry may succeed."""


@final
@dataclass(frozen=True, slots=True)
class PartSpec:
    """The authored input for producing one Part: its prompt and intrinsic index."""

    prompt: str
    index: int


class Producer(Protocol):
    """Author-to-audio backend for one Part (single-method strategy, PY-DP-11)."""

    async def produce(self, spec: PartSpec, target: Path) -> Part:
        """Generate the Part's audio into ``target`` and return the ready Part.

        Raises:
            ProducerBadInputError: a permanent error (bad_prompt / ToS / missing key).
            ProducerTransientError: a transient error (429 / quota / 5xx / timeout).
        """
        ...
