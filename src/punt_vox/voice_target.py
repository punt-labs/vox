"""Identify a voice roster by provider and optional model."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, final, runtime_checkable


@runtime_checkable
class RosterClient(Protocol):
    """The daemon read shared by clients that list voices."""

    def voices(self, target: VoiceTarget, /) -> list[str]:
        """List voices for the explicitly selected provider and model."""
        ...


@final
@dataclass(frozen=True, slots=True)
class VoiceTarget:
    """Select a provider and optionally a model within it."""

    provider: str
    model: str | None = None

    @property
    def model_field(self) -> str:
        """Return the model's frontmatter value (empty clears it)."""
        return self.model or ""

    @staticmethod
    def request(provider: str | VoiceTarget) -> dict[str, object]:
        """Build the roster request, retaining provider-only callers."""
        target = VoiceTarget(provider) if isinstance(provider, str) else provider
        message: dict[str, object] = {"type": "voices", "provider": target.provider}
        if model := target.model_field.strip():
            message["model"] = model
        return message

    def require_current_model(self, fields: dict[str, str]) -> None:
        """Reject persisting a voice after the active model changes."""
        active = fields.get("model")
        if (self.model or "").strip() != (active or "").strip():
            msg = (
                f"The active model changed from {self.model!r} to {active!r}. "
                "Retry setting the voice."
            )
            raise ValueError(msg)

    @classmethod
    def for_listing(
        cls, override: str, active: str | None, model: str | None
    ) -> VoiceTarget:
        """Use the configured model only when listing its own provider."""
        if override.lower() == (active or "").lower():
            return cls(override, model)
        return cls(override)
