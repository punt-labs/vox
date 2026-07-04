"""Result value type for the synthesis pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

__all__ = ["SynthesisOutcome"]


@dataclass(frozen=True, slots=True)
class SynthesisOutcome:
    """Audio path plus whether it came from the content-addressed cache.

    ``cached`` is ``True`` when the request was served from an existing
    on-disk cache entry (no TTS call) and ``False`` when audio was
    synthesized fresh. The daemon forwards this fact to the client so a
    caller can confirm a cache hit occurred.
    """

    path: Path
    cached: bool
