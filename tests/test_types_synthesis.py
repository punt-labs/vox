"""Tests for punt_vox.types_synthesis -- SynthesisSpec dataclass."""

from __future__ import annotations

import pytest

from punt_vox.types_synthesis import DEFAULT_RATE, SynthesisSpec


class TestSynthesisSpecValidate:
    """SynthesisSpec.validate rejects out-of-range voice settings."""

    def test_valid_all_none(self) -> None:
        spec = SynthesisSpec()
        spec.validate()  # should not raise

    def test_valid_in_range(self) -> None:
        spec = SynthesisSpec(stability=0.5, similarity=0.8, style=0.3)
        spec.validate()

    def test_valid_boundary_zero(self) -> None:
        spec = SynthesisSpec(stability=0.0, similarity=0.0, style=0.0)
        spec.validate()

    def test_valid_boundary_one(self) -> None:
        spec = SynthesisSpec(stability=1.0, similarity=1.0, style=1.0)
        spec.validate()

    def test_stability_too_high(self) -> None:
        spec = SynthesisSpec(stability=1.5)
        with pytest.raises(ValueError, match="stability"):
            spec.validate()

    def test_stability_negative(self) -> None:
        spec = SynthesisSpec(stability=-0.1)
        with pytest.raises(ValueError, match="stability"):
            spec.validate()

    def test_similarity_too_high(self) -> None:
        spec = SynthesisSpec(similarity=2.0)
        with pytest.raises(ValueError, match="similarity"):
            spec.validate()

    def test_style_too_high(self) -> None:
        spec = SynthesisSpec(style=1.01)
        with pytest.raises(ValueError, match="style"):
            spec.validate()

    def test_error_message_includes_value(self) -> None:
        spec = SynthesisSpec(stability=5.0)
        with pytest.raises(ValueError, match=r"5\.0"):
            spec.validate()


class TestSynthesisSpecToClientKwargs:
    """SynthesisSpec.to_client_kwargs omits None values."""

    def test_empty_spec_carries_default_rate(self) -> None:
        # rate is the one field always present: an unset rate falls back to
        # DEFAULT_RATE so the wire message never omits speed (regression: a
        # missing rate let providers default to 100 instead of 90).
        spec = SynthesisSpec()
        assert spec.to_client_kwargs() == {"rate": DEFAULT_RATE}

    def test_unset_rate_defaults_to_90(self) -> None:
        spec = SynthesisSpec(voice="roger")
        assert spec.to_client_kwargs()["rate"] == 90
        assert DEFAULT_RATE == 90

    def test_explicit_rate_overrides_default(self) -> None:
        spec = SynthesisSpec(rate=120)
        assert spec.to_client_kwargs()["rate"] == 120

    def test_includes_non_none_values(self) -> None:
        spec = SynthesisSpec(voice="matilda", provider="elevenlabs", rate=90)
        kwargs = spec.to_client_kwargs()
        assert kwargs == {"voice": "matilda", "provider": "elevenlabs", "rate": 90}

    def test_omits_none_values(self) -> None:
        spec = SynthesisSpec(voice="roger", stability=None, similarity=0.7)
        kwargs = spec.to_client_kwargs()
        assert "stability" not in kwargs
        assert kwargs["similarity"] == 0.7
        assert kwargs["voice"] == "roger"

    def test_includes_all_fields_when_set(self) -> None:
        spec = SynthesisSpec(
            voice="matilda",
            language="de",
            rate=90,
            provider="elevenlabs",
            model="eleven_v3",
            stability=0.5,
            similarity=0.8,
            style=0.3,
            speaker_boost=True,
            api_key="sk-test",
            vibe_tags="[warm]",
        )
        kwargs = spec.to_client_kwargs()
        assert len(kwargs) == 11
        assert kwargs["voice"] == "matilda"
        assert kwargs["language"] == "de"
        assert kwargs["rate"] == 90
        assert kwargs["provider"] == "elevenlabs"
        assert kwargs["model"] == "eleven_v3"
        assert kwargs["stability"] == 0.5
        assert kwargs["similarity"] == 0.8
        assert kwargs["style"] == 0.3
        assert kwargs["speaker_boost"] is True
        assert kwargs["api_key"] == "sk-test"
        assert kwargs["vibe_tags"] == "[warm]"

    def test_once_not_included(self) -> None:
        """The ``once`` field is a CLI/handler concern, not a client kwarg."""
        spec = SynthesisSpec(voice="roger", once=True)
        kwargs = spec.to_client_kwargs()
        assert "once" not in kwargs

    def test_speaker_boost_false_included(self) -> None:
        """``speaker_boost=False`` is distinct from None and must be forwarded."""
        spec = SynthesisSpec(speaker_boost=False)
        kwargs = spec.to_client_kwargs()
        assert kwargs["speaker_boost"] is False


class TestSynthesisSpecFrozen:
    """SynthesisSpec is immutable."""

    def test_cannot_assign(self) -> None:
        spec = SynthesisSpec(voice="matilda")
        with pytest.raises(AttributeError):
            spec.voice = "roger"  # type: ignore[misc]

    def test_has_slots(self) -> None:
        spec = SynthesisSpec()
        assert not hasattr(spec, "__dict__")
