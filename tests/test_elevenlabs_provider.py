"""Tests for punt_vox.providers.elevenlabs."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from punt_vox.providers.elevenlabs import (
    _VOICE_CACHE_TTL_S,  # pyright: ignore[reportPrivateUsage]
    ElevenLabsProvider,
)
from punt_vox.types import SynthesisRequest, VoiceNotFoundError


class TestElevenLabsProviderName:
    def test_name(self, elevenlabs_provider: ElevenLabsProvider) -> None:
        assert elevenlabs_provider.name == "elevenlabs"

    def test_default_voice(self, elevenlabs_provider: ElevenLabsProvider) -> None:
        assert elevenlabs_provider.default_voice == "matilda"


class TestElevenLabsProviderResolveVoice:
    def test_resolve_cached_voice(
        self, elevenlabs_provider: ElevenLabsProvider
    ) -> None:
        result = elevenlabs_provider.resolve_voice("matilda")
        assert result == "matilda"

    def test_resolve_case_insensitive(
        self, elevenlabs_provider: ElevenLabsProvider
    ) -> None:
        assert elevenlabs_provider.resolve_voice("Matilda") == "matilda"
        assert elevenlabs_provider.resolve_voice("MATILDA") == "matilda"

    def test_resolve_voice_id_directly(
        self, elevenlabs_provider: ElevenLabsProvider
    ) -> None:
        voice_id = "XrExE9yKIg1WjnnlVkGX"
        assert elevenlabs_provider.resolve_voice(voice_id) == voice_id

    def test_resolve_from_api(self, mock_elevenlabs_client: MagicMock) -> None:
        """Voice not in cache triggers API fetch."""
        import punt_vox.providers.elevenlabs as elevenlabs

        saved_voices = dict(elevenlabs.VOICES)
        saved_loaded = elevenlabs._voices_loaded_at  # pyright: ignore[reportPrivateUsage]

        elevenlabs.VOICES.clear()
        elevenlabs._voices_loaded_at = 0.0  # pyright: ignore[reportPrivateUsage]

        try:
            provider = ElevenLabsProvider(client=mock_elevenlabs_client)
            result = provider.resolve_voice("matilda")
            assert result == "matilda"
            mock_elevenlabs_client.voices.get_all.assert_called_once()
        finally:
            elevenlabs.VOICES.clear()
            elevenlabs.VOICES.update(saved_voices)
            elevenlabs._voices_loaded_at = saved_loaded  # pyright: ignore[reportPrivateUsage]

    def test_resolve_short_name_from_api_with_descriptions(
        self, mock_elevenlabs_client: MagicMock
    ) -> None:
        """Lookup of 'matilda' works when API returns description."""
        import punt_vox.providers.elevenlabs as elevenlabs

        saved_voices = dict(elevenlabs.VOICES)
        saved_loaded = elevenlabs._voices_loaded_at  # pyright: ignore[reportPrivateUsage]

        elevenlabs.VOICES.clear()
        elevenlabs._voices_loaded_at = 0.0  # pyright: ignore[reportPrivateUsage]

        try:
            provider = ElevenLabsProvider(client=mock_elevenlabs_client)
            # Should resolve "matilda" even though API returned
            # "Matilda - Knowledgable, Professional".
            result = provider.resolve_voice("matilda")
            assert result == "matilda"

            # Full name with description should also work.
            result2 = provider.resolve_voice("Matilda - Knowledgable, Professional")
            assert result2 == "matilda - knowledgable, professional"
        finally:
            elevenlabs.VOICES.clear()
            elevenlabs.VOICES.update(saved_voices)
            elevenlabs._voices_loaded_at = saved_loaded  # pyright: ignore[reportPrivateUsage]

    def test_resolve_unknown_raises(
        self, elevenlabs_provider: ElevenLabsProvider
    ) -> None:
        with pytest.raises(VoiceNotFoundError) as exc_info:
            elevenlabs_provider.resolve_voice("nonexistent")
        assert exc_info.value.voice_name == "nonexistent"
        assert isinstance(exc_info.value.available, list)
        assert len(exc_info.value.available) > 0


class TestElevenLabsProviderSynthesize:
    def test_synthesize_creates_file(
        self,
        elevenlabs_provider: ElevenLabsProvider,
        tmp_output_dir: Path,
    ) -> None:
        request = SynthesisRequest(text="hello", voice="matilda", rate=100)
        out = tmp_output_dir / "test.mp3"

        result = elevenlabs_provider.synthesize(request, out)

        assert result.path == out
        assert out.exists()
        assert out.stat().st_size > 0

    def test_synthesize_result_metadata(
        self,
        elevenlabs_provider: ElevenLabsProvider,
        tmp_output_dir: Path,
    ) -> None:
        request = SynthesisRequest(text="hello world", voice="matilda", rate=100)
        out = tmp_output_dir / "meta.mp3"

        result = elevenlabs_provider.synthesize(request, out)

        assert result.text == "hello world"
        assert result.voice == "matilda"
        assert result.path == out

    def test_synthesize_passes_model(
        self,
        mock_elevenlabs_client: MagicMock,
        tmp_output_dir: Path,
    ) -> None:
        provider = ElevenLabsProvider(
            model="eleven_turbo_v2_5", client=mock_elevenlabs_client
        )
        request = SynthesisRequest(text="test", voice="matilda", rate=100)
        out = tmp_output_dir / "model.mp3"

        provider.synthesize(request, out)

        call_kwargs = mock_elevenlabs_client.text_to_speech.stream.call_args.kwargs
        assert call_kwargs["model_id"] == "eleven_turbo_v2_5"

    def test_synthesize_with_voice_settings(
        self,
        mock_elevenlabs_client: MagicMock,
        elevenlabs_provider: ElevenLabsProvider,
        tmp_output_dir: Path,
    ) -> None:
        request = SynthesisRequest(
            text="test",
            voice="matilda",
            rate=100,
            stability=0.5,
            similarity=0.7,
            style=0.3,
            speaker_boost=True,
        )
        out = tmp_output_dir / "settings.mp3"

        elevenlabs_provider.synthesize(request, out)

        call_kwargs = mock_elevenlabs_client.text_to_speech.stream.call_args.kwargs
        assert "voice_settings" in call_kwargs
        vs = call_kwargs["voice_settings"]
        assert vs.stability == 0.5
        assert vs.similarity_boost == 0.7
        assert vs.style == 0.3
        assert vs.use_speaker_boost is True

    def test_synthesize_without_voice_settings(
        self,
        mock_elevenlabs_client: MagicMock,
        elevenlabs_provider: ElevenLabsProvider,
        tmp_output_dir: Path,
    ) -> None:
        request = SynthesisRequest(text="test", voice="matilda", rate=100)
        out = tmp_output_dir / "no_settings.mp3"

        elevenlabs_provider.synthesize(request, out)

        call_kwargs = mock_elevenlabs_client.text_to_speech.stream.call_args.kwargs
        assert "voice_settings" not in call_kwargs

    def test_synthesize_chunked_text(
        self,
        mock_elevenlabs_client: MagicMock,
        tmp_output_dir: Path,
    ) -> None:
        """Text exceeding model char limit triggers chunked synthesis."""
        provider = ElevenLabsProvider(
            model="eleven_turbo_v2", client=mock_elevenlabs_client
        )
        # eleven_turbo_v2 limit = 10,000 chars
        long_text = "Hello. " * 2000  # ~14000 chars
        request = SynthesisRequest(text=long_text.strip(), voice="matilda", rate=100)
        out = tmp_output_dir / "chunked.mp3"

        result = provider.synthesize(request, out)

        assert result.path == out
        assert out.exists()
        assert mock_elevenlabs_client.text_to_speech.stream.call_count > 1


class TestElevenLabsProviderCheckHealth:
    @patch.dict("os.environ", {"ELEVENLABS_API_KEY": "test-key"})
    def test_all_pass(self, mock_elevenlabs_client: MagicMock) -> None:
        provider = ElevenLabsProvider(client=mock_elevenlabs_client)
        checks = provider.check_health()

        assert len(checks) == 2
        assert all(c.passed for c in checks)
        assert "API key: set" in checks[0].message
        assert "free" in checks[1].message
        assert "500" in checks[1].message
        assert "10,000" in checks[1].message

    @patch.dict("os.environ", {}, clear=True)
    def test_missing_api_key(self) -> None:
        import os

        os.environ.pop("ELEVENLABS_API_KEY", None)

        provider = ElevenLabsProvider(client=MagicMock())
        checks = provider.check_health()

        assert len(checks) == 1
        assert not checks[0].passed
        assert "not set" in checks[0].message

    @patch.dict("os.environ", {"ELEVENLABS_API_KEY": "test-key"})
    def test_auth_error(self, mock_elevenlabs_client: MagicMock) -> None:
        from elevenlabs.core import ApiError  # pyright: ignore[reportMissingTypeStubs]

        mock_elevenlabs_client.user.subscription.get.side_effect = ApiError(
            status_code=401
        )

        provider = ElevenLabsProvider(client=mock_elevenlabs_client)
        checks = provider.check_health()

        assert len(checks) == 2
        assert checks[0].passed  # Key is set.
        assert not checks[1].passed


class TestElevenLabsProviderRateMessage:
    def test_logs_debug_when_rate_not_100(
        self,
        elevenlabs_provider: ElevenLabsProvider,
        tmp_output_dir: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        request = SynthesisRequest(text="test", voice="matilda", rate=90)
        out = tmp_output_dir / "rate.mp3"

        with caplog.at_level(logging.DEBUG):
            elevenlabs_provider.synthesize(request, out)

        assert "does not support rate adjustment" in caplog.text
        assert "rate=90" in caplog.text

    def test_no_message_at_rate_100(
        self,
        elevenlabs_provider: ElevenLabsProvider,
        tmp_output_dir: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        request = SynthesisRequest(text="test", voice="matilda", rate=100)
        out = tmp_output_dir / "rate100.mp3"

        with caplog.at_level(logging.DEBUG):
            elevenlabs_provider.synthesize(request, out)

        assert "does not support rate adjustment" not in caplog.text


class TestElevenLabsProviderDefaultModel:
    def test_default_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Clear TTS_MODEL to validate the code default, not an env override.
        monkeypatch.delenv("TTS_MODEL", raising=False)
        provider = ElevenLabsProvider(client=MagicMock())
        assert provider._model == "eleven_v3"  # pyright: ignore[reportPrivateUsage]

    def test_default_supports_expressive_tags(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The default model must render /vibe tags as performance cues.

        The /vibe feature is the headline product differentiator; a default
        model that can't interpret bracket-style tags silently breaks it
        for every user who never sets TTS_MODEL explicitly.
        """
        # Clear TTS_MODEL to validate the code default, not an env override.
        monkeypatch.delenv("TTS_MODEL", raising=False)
        provider = ElevenLabsProvider(client=MagicMock())
        assert provider.supports_expressive_tags is True

    def test_explicit_model(self) -> None:
        provider = ElevenLabsProvider(model="eleven_turbo_v2_5", client=MagicMock())
        assert provider._model == "eleven_turbo_v2_5"  # pyright: ignore[reportPrivateUsage]

    @patch.dict("os.environ", {"TTS_MODEL": "eleven_turbo_v2"})
    def test_model_from_env(self) -> None:
        provider = ElevenLabsProvider(client=MagicMock())
        assert provider._model == "eleven_turbo_v2"  # pyright: ignore[reportPrivateUsage]

    @patch.dict("os.environ", {"TTS_MODEL": "eleven_turbo_v2"})
    def test_explicit_overrides_env(self) -> None:
        provider = ElevenLabsProvider(model="eleven_v3", client=MagicMock())
        assert provider._model == "eleven_v3"  # pyright: ignore[reportPrivateUsage]

    def test_expressive_tags_supported_on_v3(self) -> None:
        provider = ElevenLabsProvider(model="eleven_v3", client=MagicMock())
        assert provider.supports_expressive_tags is True

    def test_expressive_tags_not_supported_on_flash(self) -> None:
        provider = ElevenLabsProvider(model="eleven_flash_v2_5", client=MagicMock())
        assert provider.supports_expressive_tags is False

    def test_expressive_tags_not_supported_on_turbo(self) -> None:
        provider = ElevenLabsProvider(model="eleven_turbo_v2_5", client=MagicMock())
        assert provider.supports_expressive_tags is False

    def test_model_supports_expressive_tags_classmethod_v3(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Static lookup must return True for eleven_v3 without instantiation."""
        monkeypatch.delenv("TTS_MODEL", raising=False)
        assert ElevenLabsProvider.model_supports_expressive_tags("eleven_v3") is True

    def test_model_supports_expressive_tags_classmethod_flash(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Static lookup must return False for non-expressive models."""
        monkeypatch.delenv("TTS_MODEL", raising=False)
        assert (
            ElevenLabsProvider.model_supports_expressive_tags("eleven_flash_v2_5")
            is False
        )

    def test_model_supports_expressive_tags_classmethod_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """None argument must resolve to the code default (eleven_v3 → True)."""
        monkeypatch.delenv("TTS_MODEL", raising=False)
        assert ElevenLabsProvider.model_supports_expressive_tags(None) is True

    def test_model_supports_expressive_tags_classmethod_env_override(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When model arg is None, the TTS_MODEL env var wins over the default."""
        monkeypatch.setenv("TTS_MODEL", "eleven_flash_v2_5")
        assert ElevenLabsProvider.model_supports_expressive_tags(None) is False

    def test_model_supports_expressive_tags_classmethod_explicit_beats_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Explicit model arg wins over the TTS_MODEL env var."""
        monkeypatch.setenv("TTS_MODEL", "eleven_flash_v2_5")
        assert ElevenLabsProvider.model_supports_expressive_tags("eleven_v3") is True


class TestElevenLabsProviderCharLimits:
    def test_v3_chunks_above_5k(self) -> None:
        """eleven_v3 should chunk text exceeding 5,000 chars."""
        provider = ElevenLabsProvider(model="eleven_v3", client=MagicMock())
        text = "a" * 5_001
        request = SynthesisRequest(text=text, voice="matilda")
        with (
            patch.object(provider, "_chunked_synthesize") as mock_chunked,
            patch.object(provider, "_single_synthesize"),
        ):
            provider.synthesize(request, Path("/tmp/out.mp3"))
            mock_chunked.assert_called_once()

    def test_flash_single_call_under_40k(self) -> None:
        """eleven_flash_v2_5 should use single call for text under 40k."""
        provider = ElevenLabsProvider(model="eleven_flash_v2_5", client=MagicMock())
        text = "a" * 10_000
        request = SynthesisRequest(text=text, voice="matilda")
        with (
            patch.object(provider, "_single_synthesize") as mock_single,
            patch.object(provider, "_chunked_synthesize"),
        ):
            provider.synthesize(request, Path("/tmp/out.mp3"))
            mock_single.assert_called_once()


class TestElevenLabsProviderLanguageSupport:
    def test_resolve_voice_with_language(
        self, elevenlabs_provider: ElevenLabsProvider
    ) -> None:
        result = elevenlabs_provider.resolve_voice("matilda", language="de")
        assert result == "matilda"

    def test_get_default_voice_any_language(
        self, elevenlabs_provider: ElevenLabsProvider
    ) -> None:
        assert elevenlabs_provider.get_default_voice("de") == "matilda"
        assert elevenlabs_provider.get_default_voice("ja") == "matilda"

    def test_list_voices_returns_short_names(
        self, elevenlabs_provider: ElevenLabsProvider
    ) -> None:
        voices = elevenlabs_provider.list_voices()
        assert "drew" in voices
        assert "matilda" in voices
        for v in voices:
            assert " - " not in v

    def test_list_voices_ignores_language(
        self, elevenlabs_provider: ElevenLabsProvider
    ) -> None:
        all_voices = elevenlabs_provider.list_voices()
        filtered = elevenlabs_provider.list_voices(language="de")
        assert all_voices == filtered

    def test_list_voices_sorted(self, elevenlabs_provider: ElevenLabsProvider) -> None:
        voices = elevenlabs_provider.list_voices()
        assert voices == sorted(voices)

    def test_infer_language_returns_none(
        self, elevenlabs_provider: ElevenLabsProvider
    ) -> None:
        assert elevenlabs_provider.infer_language_from_voice("matilda") is None

    def test_synthesize_preserves_language(
        self,
        elevenlabs_provider: ElevenLabsProvider,
        tmp_output_dir: Path,
    ) -> None:
        request = SynthesisRequest(
            text="Guten Tag", voice="matilda", rate=100, language="de"
        )
        result = elevenlabs_provider.synthesize(request, tmp_output_dir / "test.mp3")
        assert result.language == "de"

    def test_synthesize_no_language(
        self,
        elevenlabs_provider: ElevenLabsProvider,
        tmp_output_dir: Path,
    ) -> None:
        request = SynthesisRequest(text="hello", voice="matilda", rate=100)
        result = elevenlabs_provider.synthesize(request, tmp_output_dir / "test.mp3")
        assert result.language is None


class TestElevenLabsVoiceCacheTTL:
    """Voice cache TTL: stale cache triggers re-fetch, fresh cache does not."""

    def test_no_refetch_within_ttl(self, mock_elevenlabs_client: MagicMock) -> None:
        """Within TTL, a second call does not re-fetch from the API."""
        import punt_vox.providers.elevenlabs as elevenlabs

        saved_voices = dict(elevenlabs.VOICES)
        saved_loaded_at = elevenlabs._voices_loaded_at  # pyright: ignore[reportPrivateUsage]

        elevenlabs.VOICES.clear()
        elevenlabs._voices_loaded_at = 0.0  # pyright: ignore[reportPrivateUsage]

        try:
            provider = ElevenLabsProvider(client=mock_elevenlabs_client)
            # First call fetches.
            provider.list_voices()
            assert mock_elevenlabs_client.voices.get_all.call_count == 1

            # Second call within TTL does not re-fetch.
            provider.list_voices()
            assert mock_elevenlabs_client.voices.get_all.call_count == 1
        finally:
            elevenlabs.VOICES.clear()
            elevenlabs.VOICES.update(saved_voices)
            elevenlabs._voices_loaded_at = saved_loaded_at  # pyright: ignore[reportPrivateUsage]

    def test_refetch_after_ttl_expires(self, mock_elevenlabs_client: MagicMock) -> None:
        """After TTL expires, the next call re-fetches from the API."""
        import punt_vox.providers.elevenlabs as elevenlabs

        saved_voices = dict(elevenlabs.VOICES)
        saved_loaded_at = elevenlabs._voices_loaded_at  # pyright: ignore[reportPrivateUsage]

        elevenlabs.VOICES.clear()
        elevenlabs._voices_loaded_at = 0.0  # pyright: ignore[reportPrivateUsage]

        try:
            provider = ElevenLabsProvider(client=mock_elevenlabs_client)
            # First call fetches.
            provider.list_voices()
            assert mock_elevenlabs_client.voices.get_all.call_count == 1

            # Simulate TTL expiry by pushing the timestamp into the past.
            elevenlabs._voices_loaded_at = time.monotonic() - _VOICE_CACHE_TTL_S - 1  # pyright: ignore[reportPrivateUsage]

            # Next call re-fetches.
            provider.list_voices()
            assert mock_elevenlabs_client.voices.get_all.call_count == 2
        finally:
            elevenlabs.VOICES.clear()
            elevenlabs.VOICES.update(saved_voices)
            elevenlabs._voices_loaded_at = saved_loaded_at  # pyright: ignore[reportPrivateUsage]

    def test_new_voice_found_after_ttl_expiry(
        self, mock_elevenlabs_client: MagicMock
    ) -> None:
        """A voice added after initial fetch is found after TTL expiry."""
        import punt_vox.providers.elevenlabs as elevenlabs

        saved_voices = dict(elevenlabs.VOICES)
        saved_loaded_at = elevenlabs._voices_loaded_at  # pyright: ignore[reportPrivateUsage]
        saved_force_at = elevenlabs._voices_force_fetched_at  # pyright: ignore[reportPrivateUsage]

        elevenlabs.VOICES.clear()
        elevenlabs._voices_loaded_at = 0.0  # pyright: ignore[reportPrivateUsage]
        elevenlabs._voices_force_fetched_at = 0.0  # pyright: ignore[reportPrivateUsage]

        try:
            provider = ElevenLabsProvider(client=mock_elevenlabs_client)
            # First fetch: only matilda and drew.
            provider.list_voices()

            # The new voice "aria" does not exist yet.
            with pytest.raises(VoiceNotFoundError):
                provider.resolve_voice("aria")

            # Simulate: user adds "aria" to their ElevenLabs account.
            voice_aria = MagicMock()
            voice_aria.name = "Aria - Expressive, warm"
            voice_aria.voice_id = "9BWtsMINqrJLrRacOk9x"

            old_voices = mock_elevenlabs_client.voices.get_all.return_value.voices
            updated_response = MagicMock()
            updated_response.voices = [*list(old_voices), voice_aria]
            mock_elevenlabs_client.voices.get_all.return_value = updated_response

            # Expire the cache and reset force cooldown so the next
            # miss triggers a fresh fetch.
            elevenlabs._voices_loaded_at = time.monotonic() - _VOICE_CACHE_TTL_S - 1  # pyright: ignore[reportPrivateUsage]
            elevenlabs._voices_force_fetched_at = 0.0  # pyright: ignore[reportPrivateUsage]

            # Now "aria" resolves after re-fetch.
            result = provider.resolve_voice("aria")
            assert result == "aria"
        finally:
            elevenlabs.VOICES.clear()
            elevenlabs.VOICES.update(saved_voices)
            elevenlabs._voices_loaded_at = saved_loaded_at  # pyright: ignore[reportPrivateUsage]
            elevenlabs._voices_force_fetched_at = saved_force_at  # pyright: ignore[reportPrivateUsage]

    def test_deleted_voice_removed_after_ttl_expiry(
        self, mock_elevenlabs_client: MagicMock
    ) -> None:
        """A voice removed from the account disappears after TTL expiry."""
        import punt_vox.providers.elevenlabs as elevenlabs

        saved_voices = dict(elevenlabs.VOICES)
        saved_loaded_at = elevenlabs._voices_loaded_at  # pyright: ignore[reportPrivateUsage]

        elevenlabs.VOICES.clear()
        elevenlabs._voices_loaded_at = 0.0  # pyright: ignore[reportPrivateUsage]

        try:
            provider = ElevenLabsProvider(client=mock_elevenlabs_client)
            # First fetch: matilda and drew both present.
            voices = provider.list_voices()
            assert "drew" in voices

            # Simulate: user removes "drew" from their account.
            voice_matilda = MagicMock()
            voice_matilda.name = "Matilda - Knowledgable, Professional"
            voice_matilda.voice_id = "XrExE9yKIg1WjnnlVkGX"
            updated_response = MagicMock()
            updated_response.voices = [voice_matilda]
            mock_elevenlabs_client.voices.get_all.return_value = updated_response

            # Expire the cache.
            elevenlabs._voices_loaded_at = time.monotonic() - _VOICE_CACHE_TTL_S - 1  # pyright: ignore[reportPrivateUsage]

            # After re-fetch, "drew" is gone.
            voices_after = provider.list_voices()
            assert "drew" not in voices_after
        finally:
            elevenlabs.VOICES.clear()
            elevenlabs.VOICES.update(saved_voices)
            elevenlabs._voices_loaded_at = saved_loaded_at  # pyright: ignore[reportPrivateUsage]

    def test_cache_miss_forces_single_refetch_then_cooldown(
        self, mock_elevenlabs_client: MagicMock
    ) -> None:
        """Cache miss forces one re-fetch; repeated misses within cooldown don't."""
        import punt_vox.providers.elevenlabs as elevenlabs

        saved_voices = dict(elevenlabs.VOICES)
        saved_loaded_at = elevenlabs._voices_loaded_at  # pyright: ignore[reportPrivateUsage]
        saved_force_at = elevenlabs._voices_force_fetched_at  # pyright: ignore[reportPrivateUsage]

        elevenlabs.VOICES.clear()
        elevenlabs._voices_loaded_at = 0.0  # pyright: ignore[reportPrivateUsage]
        elevenlabs._voices_force_fetched_at = 0.0  # pyright: ignore[reportPrivateUsage]

        try:
            provider = ElevenLabsProvider(client=mock_elevenlabs_client)
            # Initial fetch populates the cache.
            provider.list_voices()
            assert mock_elevenlabs_client.voices.get_all.call_count == 1

            # "zzz_unknown" is not in the cache — triggers a forced re-fetch.
            with pytest.raises(VoiceNotFoundError):
                provider.resolve_voice("zzz_unknown")
            assert mock_elevenlabs_client.voices.get_all.call_count == 2

            # Same unknown voice again within cooldown — no additional fetch.
            with pytest.raises(VoiceNotFoundError):
                provider.resolve_voice("zzz_unknown")
            assert mock_elevenlabs_client.voices.get_all.call_count == 2

            # A different unknown voice within cooldown — still no fetch.
            with pytest.raises(VoiceNotFoundError):
                provider.resolve_voice("yyy_also_unknown")
            assert mock_elevenlabs_client.voices.get_all.call_count == 2
        finally:
            elevenlabs.VOICES.clear()
            elevenlabs.VOICES.update(saved_voices)
            elevenlabs._voices_loaded_at = saved_loaded_at  # pyright: ignore[reportPrivateUsage]
            elevenlabs._voices_force_fetched_at = saved_force_at  # pyright: ignore[reportPrivateUsage]
