"""Failures safe to report outside the ElevenLabs wire boundary."""

from __future__ import annotations

__all__ = ["ConvAIError", "ForeignResourceError"]


class ConvAIError(RuntimeError):
    """Report a Conv AI failure without including credentials or payloads."""


class ForeignResourceError(ConvAIError):
    """Refuse to delete a remote resource that voxd on this host did not create."""
