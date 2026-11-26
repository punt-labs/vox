"""Failures safe to report outside the ElevenLabs wire boundary."""

from __future__ import annotations

__all__ = ["ConvAIError", "ForeignResourceError", "UnknownCreateError"]


class ConvAIError(RuntimeError):
    """Report a Conv AI failure without including credentials or payloads."""


class ForeignResourceError(ConvAIError):
    """Refuse to delete a remote resource that voxd on this host did not create."""


class UnknownCreateError(ConvAIError):
    """Report a create whose outcome is unknown: it may have committed remotely."""
