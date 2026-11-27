"""Failures safe to report outside the ElevenLabs wire boundary."""

from __future__ import annotations

from typing import cast

__all__ = [
    "ConvAIError",
    "ForeignResourceError",
    "SweepIncompleteError",
    "UnknownCreateError",
]


class ConvAIError(RuntimeError):
    """Report a Conv AI failure without including credentials or payloads."""


class ForeignResourceError(ConvAIError):
    """Refuse to delete a remote resource that voxd on this host did not create."""


class UnknownCreateError(ConvAIError):
    """Report a create whose outcome is unknown: it may have committed remotely."""


class SweepIncompleteError(ConvAIError):
    """Report a listing walk cut short, with the marked ids it had already found.

    ``args`` is the message and then the found ids.
    """

    @property
    def found(self) -> tuple[str, ...]:
        """Return the marked ids found before the walk stopped."""
        return cast("tuple[str, ...]", self.args[1])
