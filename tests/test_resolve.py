"""Tests for resolve_voice_and_language in resolve.py.

The helper functions split_leading_expressive_tags, strip_expressive_tags,
and apply_vibe are covered by tests/test_server.py.  This file targets the
six conditional paths and three edge cases in resolve_voice_and_language.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from punt_vox.resolve import resolve_voice_and_language
from punt_vox.types import TTSProvider, VoiceNotFoundError


def _make_provider(
    *,
    default_voice: str = "Rachel",
    resolve_voice_return: str | None = "Rachel",
    infer_language_return: str | None = "en",
    get_default_voice_return: str = "Rachel",
) -> MagicMock:
    """Return a MagicMock configured to satisfy the TTSProvider protocol.

    Sets all protocol attributes explicitly so no MagicMock object leaks
    into string-interpolated log calls or equality assertions.
    """
    mock: MagicMock = MagicMock(spec=TTSProvider)
    mock.default_voice = default_voice
    mock.resolve_voice.return_value = resolve_voice_return
    mock.infer_language_from_voice.return_value = infer_language_return
    mock.get_default_voice.return_value = get_default_voice_return
    return mock


class TestResolveVoiceAndLanguage:
    """Six paths and three edge cases for resolve_voice_and_language."""

    # ------------------------------------------------------------------
    # P1: explicit voice + explicit language
    # ------------------------------------------------------------------

    def test_explicit_voice_and_language(self) -> None:
        """Validate language, resolve voice with both; do not infer language."""
        provider = _make_provider()
        voice, language = resolve_voice_and_language(provider, "Joanna", "en")

        # resolve_voice return value is discarded; voice stays as the input.
        assert voice == "Joanna"
        assert language == "en"
        provider.resolve_voice.assert_called_once_with("Joanna", "en")
        provider.infer_language_from_voice.assert_not_called()
        provider.get_default_voice.assert_not_called()

    # ------------------------------------------------------------------
    # P2: explicit voice + no language
    # ------------------------------------------------------------------

    def test_explicit_voice_infers_language(self) -> None:
        """Resolve voice without language; infer language from the voice."""
        provider = _make_provider(infer_language_return="en")
        voice, language = resolve_voice_and_language(provider, "Joanna", None)

        # resolve_voice return value is discarded; voice stays as the input.
        assert voice == "Joanna"
        assert language == "en"
        provider.resolve_voice.assert_called_once_with("Joanna")
        provider.infer_language_from_voice.assert_called_once_with("Joanna")
        provider.get_default_voice.assert_not_called()

    # ------------------------------------------------------------------
    # P3: no voice + explicit language
    # ------------------------------------------------------------------

    def test_language_only_uses_provider_default(self) -> None:
        """Get default voice for the language; resolve with both."""
        provider = _make_provider(get_default_voice_return="Vicki")
        provider.resolve_voice.return_value = "Vicki"

        with patch("punt_vox.resolve._config.read_field", return_value=None):
            voice, language = resolve_voice_and_language(provider, None, "de")

        assert voice == "Vicki"
        assert language == "de"
        provider.get_default_voice.assert_called_once_with("de")
        provider.resolve_voice.assert_called_once_with("Vicki", "de")
        provider.infer_language_from_voice.assert_not_called()

    # ------------------------------------------------------------------
    # P4: no voice + no language
    # ------------------------------------------------------------------

    def test_no_inputs_uses_provider_default(self) -> None:
        """Use provider.default_voice; resolve and infer language."""
        provider = _make_provider(default_voice="Rachel", infer_language_return="en")

        with patch("punt_vox.resolve._config.read_field", return_value=None):
            voice, language = resolve_voice_and_language(provider, None, None)

        assert voice == "Rachel"
        assert language == "en"
        provider.resolve_voice.assert_called_once_with("Rachel")
        provider.infer_language_from_voice.assert_called_once_with("Rachel")

    # ------------------------------------------------------------------
    # P5: voice from config + VoiceNotFoundError (fallback succeeds)
    # ------------------------------------------------------------------

    def test_config_voice_fallback_on_not_found(self) -> None:
        """Log warning and fall back to default_voice when config voice fails."""
        provider = _make_provider(default_voice="Rachel", infer_language_return="en")
        # First call raises (config voice rejected); second call succeeds.
        provider.resolve_voice.side_effect = [
            VoiceNotFoundError("session-voice", []),
            "Rachel",
        ]

        with patch("punt_vox.resolve._config.read_field", return_value="session-voice"):
            voice, language = resolve_voice_and_language(provider, None, None)

        assert voice == "Rachel"
        assert language == "en"
        assert provider.resolve_voice.call_count == 2

    # ------------------------------------------------------------------
    # P6: explicit voice + VoiceNotFoundError (re-raise)
    # ------------------------------------------------------------------

    def test_explicit_voice_raises_on_not_found(self) -> None:
        """Re-raise VoiceNotFoundError when the voice came from the caller."""
        provider = _make_provider()
        provider.resolve_voice.side_effect = VoiceNotFoundError("Bad", [])

        with pytest.raises(VoiceNotFoundError):
            resolve_voice_and_language(provider, "Bad", None)

    # ------------------------------------------------------------------
    # E1: invalid language code — real validate_language, not mocked
    # ------------------------------------------------------------------

    def test_invalid_language_raises(self) -> None:
        """Pass an invalid language code; validate_language raises ValueError."""
        provider = _make_provider()

        with pytest.raises(ValueError, match="Invalid language code"):
            resolve_voice_and_language(provider, "Joanna", "xx-invalid")

    # ------------------------------------------------------------------
    # E2: config voice fails, language provided — fallback uses language
    # ------------------------------------------------------------------

    def test_config_voice_fallback_with_language(self) -> None:
        """After fallback to default_voice, resolve with the provided language."""
        provider = _make_provider(default_voice="Rachel")
        provider.resolve_voice.side_effect = [
            VoiceNotFoundError("session-voice", []),
            "Rachel",
        ]

        with patch("punt_vox.resolve._config.read_field", return_value="session-voice"):
            voice, language = resolve_voice_and_language(provider, None, "en")

        assert voice == "Rachel"
        assert language == "en"
        # Second resolve_voice call must include language.
        provider.resolve_voice.assert_any_call("Rachel", "en")

    # ------------------------------------------------------------------
    # E3: config voice fails, no language — fallback infers language
    # ------------------------------------------------------------------

    def test_config_voice_fallback_no_language(self) -> None:
        """After fallback to default_voice with no language, infer from voice."""
        provider = _make_provider(default_voice="Rachel", infer_language_return="en")
        provider.resolve_voice.side_effect = [
            VoiceNotFoundError("session-voice", []),
            "Rachel",
        ]

        with patch("punt_vox.resolve._config.read_field", return_value="session-voice"):
            voice, language = resolve_voice_and_language(provider, None, None)

        assert voice == "Rachel"
        assert language == "en"
        provider.infer_language_from_voice.assert_called_once_with("Rachel")
