"""Synthesis parameter bundle for TTS requests."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["SynthesisSpec"]


@dataclass(frozen=True, slots=True)
class SynthesisSpec:
    """Bundle the synthesis parameters that travel together across CLI, MCP, and voxd.

    All fields except ``once`` default to ``None`` (unset), which means
    "use the provider/session default."  Validation happens at the
    boundary via :meth:`validate`.
    """

    voice: str | None = None
    language: str | None = None
    rate: int | None = None
    provider: str | None = None
    model: str | None = None
    stability: float | None = None
    similarity: float | None = None
    style: float | None = None
    speaker_boost: bool | None = None
    api_key: str | None = None
    vibe_tags: str | None = None
    once: bool = False

    def validate(self) -> None:
        """Validate voice settings ranges.

        Raises ``ValueError`` when stability, similarity, or style is
        outside 0.0--1.0.
        """
        for name, value in (
            ("stability", self.stability),
            ("similarity", self.similarity),
            ("style", self.style),
        ):
            if value is not None and not 0.0 <= value <= 1.0:
                msg = f"{name} must be between 0.0 and 1.0, got {value}"
                raise ValueError(msg)

    def to_client_kwargs(self) -> dict[str, object]:
        """Build a kwargs dict for ``VoxClientSync.synthesize`` / ``record``.

        Omits fields whose value is ``None`` so the client method
        receives only explicitly-set parameters.  The ``once`` field
        is included only when ``True``.
        """
        out: dict[str, object] = {}
        if self.voice is not None:
            out["voice"] = self.voice
        if self.language is not None:
            out["language"] = self.language
        if self.rate is not None:
            out["rate"] = self.rate
        if self.provider is not None:
            out["provider"] = self.provider
        if self.model is not None:
            out["model"] = self.model
        if self.stability is not None:
            out["stability"] = self.stability
        if self.similarity is not None:
            out["similarity"] = self.similarity
        if self.style is not None:
            out["style"] = self.style
        if self.speaker_boost is not None:
            out["speaker_boost"] = self.speaker_boost
        if self.api_key is not None:
            out["api_key"] = self.api_key
        if self.vibe_tags is not None:
            out["vibe_tags"] = self.vibe_tags
        return out
