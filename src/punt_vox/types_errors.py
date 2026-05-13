"""Custom exception types for punt-vox."""

from __future__ import annotations

from typing import Self

__all__ = [
    "VoiceNotFoundError",
]


class VoiceNotFoundError(ValueError):
    """Raised when a voice name cannot be resolved by a provider."""

    _voice_name: str
    _available: list[str]

    def __new__(cls, name: str, available: list[str]) -> Self:  # pyright: ignore[reportInconsistentConstructor]
        self = super().__new__(cls, name)
        self._voice_name = name
        self._available = available
        return self

    @property
    def voice_name(self) -> str:
        """Return the voice name that was not found."""
        return self._voice_name

    @property
    def available(self) -> list[str]:
        """Return a copy of the available voice names."""
        return list(self._available)
