"""Synthesis options shared by the speech and recording CLI adapters."""

from __future__ import annotations

from typing import TypedDict

__all__ = ["SynthesisOptions"]


class SynthesisOptions(TypedDict):
    """CLI options passed unchanged to the synthesis specification."""

    voice: str | None
    language: str | None
    rate: int
    provider: str | None
    model: str | None
    stability: float | None
    similarity: float | None
    style: float | None
