"""Synthesis options shared by the speech and recording CLI adapters."""

from __future__ import annotations

from typing import TypedDict

__all__ = ["SynthesisOptions"]


class SynthesisOptions(TypedDict):
    """CLI options passed unchanged to the synthesis specification."""

    voice: str | None  # Unset inherits the session or provider voice.
    language: str | None  # Unset leaves language selection to the provider.
    rate: int
    provider: str | None  # Unset inherits the session provider.
    model: str | None  # Unset inherits the session or provider model.
    stability: float | None  # Unset leaves the provider's stability unchanged.
    similarity: float | None  # Unset leaves the provider's similarity unchanged.
    style: float | None  # Unset leaves the provider's style unchanged.
