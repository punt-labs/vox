"""Synthesis parameter bundle for TTS requests."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["DEFAULT_RATE", "SynthesisSpec"]

DEFAULT_RATE = 90
"""Client-side speech rate (percent) sent when a caller sets none.

voxd forwards an absent rate to the provider unchanged, and ElevenLabs, Polly,
and espeak all default to 100. Emitting 90 at the wire boundary keeps every
CLI, MCP, and hook path at the historical speed instead of silently speeding up.
"""


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
        receives only explicitly-set parameters.  ``rate`` is the exception:
        an unset rate is sent as :data:`DEFAULT_RATE` so the wire message
        always carries a speed, preserving the historical 90% default.  The
        ``once`` field is not a wire parameter here -- callers pass it
        separately to :meth:`VoxClientSync.synthesize`.
        """
        optional: dict[str, object | None] = {
            "voice": self.voice,
            "language": self.language,
            "provider": self.provider,
            "model": self.model,
            "stability": self.stability,
            "similarity": self.similarity,
            "style": self.style,
            "speaker_boost": self.speaker_boost,
            "api_key": self.api_key,
            "vibe_tags": self.vibe_tags,
        }
        out: dict[str, object] = {k: v for k, v in optional.items() if v is not None}
        out["rate"] = self.rate if self.rate is not None else DEFAULT_RATE
        return out
