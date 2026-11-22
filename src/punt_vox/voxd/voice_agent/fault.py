"""Failures safe to report outside the ElevenLabs wire boundary."""

from __future__ import annotations

__all__ = ["ConvAIError"]


class ConvAIError(RuntimeError):
    """Report a Conv AI failure without including credentials or payloads."""
