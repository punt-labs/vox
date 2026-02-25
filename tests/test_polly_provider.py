"""Tests for punt_tts.providers.polly."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from punt_tts.providers.polly import (
    PollyProvider,
    VoiceConfig,
    _bcp47_matches_iso,  # pyright: ignore[reportPrivateUsage]
    _best_engine,  # pyright: ignore[reportPrivateUsage]
    _infer_iso_from_bcp47,  # pyright: ignore[reportPrivateUsage]
)
from punt_tts.types import SynthesisRequest


def _make_describe_voices_response(
    voices: list[dict[str, Any]],
) -> dict[str, Any]:
    """Create a mock describe_voices response."""
    return {"Voices": voices}


def _voice_entry(
    voice_id: str,
    language: str,
    engines: list[str],
) -> dict[str, Any]:
    return {
        "Id": voice_id,
        "LanguageCode": language,
        "SupportedEngines": engines,
    }


class TestVoiceConfig:
    def test_voice_config_is_frozen(self) -> None:
        cfg = VoiceConfig(voice_id="Joanna", language_code="en-US", engine="neural")
        with pytest.raises(AttributeError):
            cfg.voice_id = "Matthew"  # type: ignore[misc]


class TestBestEngine:
    def test_prefers_neural(self) -> None:
        assert _best_engine(["standard", "neural"]) == "neural"

    def test_prefers_neural_over_generative(self) -> None:
        assert _best_engine(["generative", "neural", "standard"]) == "neural"

    def test_falls_back_to_generative(self) -> None:
        assert _best_engine(["generative", "long-form"]) == "generative"

    def test_standard_only(self) -> None:
        assert _best_engine(["standard"]) == "standard"

    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="no supported engines"):
            _best_engine([])


class TestPollyProviderResolveVoice:
    def test_resolve_cached_voice(self) -> None:
        """Resolve a voice that is already in the cache (from conftest fixture)."""
        import punt_tts.providers.polly as polly

        polly.VOICES["joanna"] = VoiceConfig(
            voice_id="Joanna", language_code="en-US", engine="neural"
        )
        provider = PollyProvider(boto_client=MagicMock())
        result = provider.resolve_voice("joanna")
        assert result == "Joanna"

    @patch("punt_tts.providers.polly.boto3")
    def test_resolve_from_api(self, mock_boto3: MagicMock) -> None:
        import punt_tts.providers.polly as polly

        polly.VOICES.clear()
        polly._voices_loaded = False  # pyright: ignore[reportPrivateUsage]

        mock_client = MagicMock()
        mock_client.describe_voices.return_value = _make_describe_voices_response(
            [_voice_entry("Joanna", "en-US", ["neural", "standard"])]
        )

        provider = PollyProvider(boto_client=mock_client)
        result = provider.resolve_voice("joanna")

        assert result == "Joanna"

    @patch("punt_tts.providers.polly.boto3")
    def test_resolve_case_insensitive(self, mock_boto3: MagicMock) -> None:
        import punt_tts.providers.polly as polly

        polly.VOICES.clear()
        polly._voices_loaded = False  # pyright: ignore[reportPrivateUsage]

        mock_client = MagicMock()
        mock_client.describe_voices.return_value = _make_describe_voices_response(
            [_voice_entry("Hans", "de-DE", ["standard"])]
        )

        provider = PollyProvider(boto_client=mock_client)
        result = provider.resolve_voice("HANS")
        assert result == "Hans"

    @patch("punt_tts.providers.polly.boto3")
    def test_resolve_unknown_voice_raises(self, mock_boto3: MagicMock) -> None:
        import punt_tts.providers.polly as polly

        polly.VOICES.clear()
        polly._voices_loaded = False  # pyright: ignore[reportPrivateUsage]

        mock_client = MagicMock()
        mock_client.describe_voices.return_value = _make_describe_voices_response([])

        provider = PollyProvider(boto_client=mock_client)
        with pytest.raises(ValueError, match="Unknown voice 'nonexistent'"):
            provider.resolve_voice("nonexistent")

    @patch("punt_tts.providers.polly.boto3")
    def test_caches_api_results(self, mock_boto3: MagicMock) -> None:
        import punt_tts.providers.polly as polly

        polly.VOICES.clear()
        polly._voices_loaded = False  # pyright: ignore[reportPrivateUsage]

        mock_client = MagicMock()
        mock_client.describe_voices.return_value = _make_describe_voices_response(
            [_voice_entry("Lucia", "es-ES", ["neural", "standard"])]
        )

        provider = PollyProvider(boto_client=mock_client)
        provider.resolve_voice("lucia")
        provider.resolve_voice("lucia")

        mock_client.describe_voices.assert_called_once()


class TestPollyProviderSynthesize:
    def test_synthesize_creates_file(
        self,
        polly_provider: PollyProvider,
        tmp_output_dir: Path,
    ) -> None:
        request = SynthesisRequest(text="hello", voice="joanna", rate=75)
        out = tmp_output_dir / "test.mp3"

        result = polly_provider.synthesize(request, out)

        assert result.path == out
        assert out.exists()
        assert out.stat().st_size > 0

    def test_synthesize_uses_ssml(
        self,
        mock_boto_client: MagicMock,
        polly_provider: PollyProvider,
        tmp_output_dir: Path,
    ) -> None:
        request = SynthesisRequest(text="Hallo", voice="hans", rate=60)
        out = tmp_output_dir / "hallo.mp3"

        polly_provider.synthesize(request, out)

        call_kwargs = mock_boto_client.synthesize_speech.call_args.kwargs
        assert call_kwargs["TextType"] == "ssml"
        assert '<prosody rate="60%">' in call_kwargs["Text"]
        assert "Hallo" in call_kwargs["Text"]

    def test_synthesize_passes_voice_params(
        self,
        mock_boto_client: MagicMock,
        polly_provider: PollyProvider,
        tmp_output_dir: Path,
    ) -> None:
        request = SynthesisRequest(text="Привет", voice="tatyana")
        out = tmp_output_dir / "privet.mp3"

        polly_provider.synthesize(request, out)

        call_kwargs = mock_boto_client.synthesize_speech.call_args.kwargs
        assert call_kwargs["VoiceId"] == "Tatyana"
        assert call_kwargs["LanguageCode"] == "ru-RU"
        assert call_kwargs["Engine"] == "standard"

    def test_synthesize_result_metadata(
        self,
        polly_provider: PollyProvider,
        tmp_output_dir: Path,
    ) -> None:
        request = SynthesisRequest(text="안녕하세요", voice="seoyeon")
        out = tmp_output_dir / "korean.mp3"

        result = polly_provider.synthesize(request, out)

        assert result.text == "안녕하세요"
        assert result.voice == "Seoyeon"


class TestPollyProviderName:
    def test_name(self) -> None:
        provider = PollyProvider(boto_client=MagicMock())
        assert provider.name == "polly"


class TestPollyProviderCheckHealth:
    @patch("punt_tts.providers.polly.boto3")
    def test_all_pass(self, mock_boto3: MagicMock) -> None:
        mock_sts = MagicMock()
        mock_sts.get_caller_identity.return_value = {"Account": "123456789012"}
        mock_polly = MagicMock()
        mock_polly.describe_voices.return_value = {"Voices": []}

        def client_factory(service: str, **_kwargs: object) -> MagicMock:
            if service == "sts":
                return mock_sts
            if service == "polly":
                return mock_polly
            return MagicMock()

        mock_boto3.client.side_effect = client_factory

        provider = PollyProvider(boto_client=MagicMock())
        checks = provider.check_health()

        assert len(checks) == 2
        assert all(c.passed for c in checks)

    @patch("punt_tts.providers.polly.boto3")
    def test_no_credentials(self, mock_boto3: MagicMock) -> None:
        from botocore.exceptions import NoCredentialsError

        mock_sts = MagicMock()
        mock_sts.get_caller_identity.side_effect = NoCredentialsError()

        def client_factory(service: str, **_kwargs: object) -> MagicMock:
            if service == "sts":
                return mock_sts
            return MagicMock()

        mock_boto3.client.side_effect = client_factory

        provider = PollyProvider(boto_client=MagicMock())
        checks = provider.check_health()

        assert not checks[0].passed
        assert "not configured" in checks[0].message


class TestBcp47MatchesIso:
    def test_standard_match(self) -> None:
        assert _bcp47_matches_iso("en-US", "en") is True

    def test_standard_no_match(self) -> None:
        assert _bcp47_matches_iso("de-DE", "en") is False

    def test_arabic_special(self) -> None:
        assert _bcp47_matches_iso("arb", "ar") is True

    def test_chinese_special(self) -> None:
        assert _bcp47_matches_iso("cmn-CN", "zh") is True

    def test_unmapped_prefix(self) -> None:
        assert _bcp47_matches_iso("en-GB", "en") is True

    def test_three_letter_no_match(self) -> None:
        assert _bcp47_matches_iso("arb", "en") is False


class TestInferIsoFromBcp47:
    def test_mapped_code(self) -> None:
        assert _infer_iso_from_bcp47("en-US") == "en"

    def test_arabic(self) -> None:
        assert _infer_iso_from_bcp47("arb") == "ar"

    def test_chinese(self) -> None:
        assert _infer_iso_from_bcp47("cmn-CN") == "zh"

    def test_unmapped_prefix(self) -> None:
        assert _infer_iso_from_bcp47("en-GB") == "en"

    def test_unmapped_three_letter(self) -> None:
        assert _infer_iso_from_bcp47("xyz") is None


class TestPollyProviderResolveVoiceWithLanguage:
    def test_matching_language(self) -> None:
        provider = PollyProvider(boto_client=MagicMock())
        result = provider.resolve_voice("joanna", language="en")
        assert result == "Joanna"

    def test_mismatching_language(self) -> None:
        provider = PollyProvider(boto_client=MagicMock())
        with pytest.raises(ValueError, match="does not support language 'de'"):
            provider.resolve_voice("joanna", language="de")

    def test_no_language_still_works(self) -> None:
        provider = PollyProvider(boto_client=MagicMock())
        assert provider.resolve_voice("hans") == "Hans"


class TestPollyProviderGetDefaultVoice:
    def test_known_language(self) -> None:
        provider = PollyProvider(boto_client=MagicMock())
        assert provider.get_default_voice("de") == "vicki"

    def test_english(self) -> None:
        provider = PollyProvider(boto_client=MagicMock())
        assert provider.get_default_voice("en") == "joanna"

    def test_unknown_language(self) -> None:
        provider = PollyProvider(boto_client=MagicMock())
        with pytest.raises(ValueError, match="No default voice"):
            provider.get_default_voice("xx")

    def test_case_insensitive(self) -> None:
        provider = PollyProvider(boto_client=MagicMock())
        assert provider.get_default_voice("DE") == "vicki"


class TestPollyProviderListVoices:
    def test_all_voices(self) -> None:
        """list_voices(None) returns all cached voices."""
        provider = PollyProvider(boto_client=MagicMock())
        voices = provider.list_voices()
        assert "joanna" in voices
        assert "hans" in voices

    def test_filter_by_language(self) -> None:
        """list_voices('en') returns only English voices."""
        provider = PollyProvider(boto_client=MagicMock())
        voices = provider.list_voices(language="en")
        assert "joanna" in voices
        assert "hans" not in voices

    def test_filter_by_language_german(self) -> None:
        provider = PollyProvider(boto_client=MagicMock())
        voices = provider.list_voices(language="de")
        assert "hans" in voices
        assert "joanna" not in voices

    def test_sorted(self) -> None:
        provider = PollyProvider(boto_client=MagicMock())
        voices = provider.list_voices()
        assert voices == sorted(voices)


class TestPollyProviderInferLanguage:
    def test_english_voice(self) -> None:
        provider = PollyProvider(boto_client=MagicMock())
        assert provider.infer_language_from_voice("joanna") == "en"

    def test_german_voice(self) -> None:
        provider = PollyProvider(boto_client=MagicMock())
        assert provider.infer_language_from_voice("hans") == "de"

    def test_russian_voice(self) -> None:
        provider = PollyProvider(boto_client=MagicMock())
        assert provider.infer_language_from_voice("tatyana") == "ru"

    def test_korean_voice(self) -> None:
        provider = PollyProvider(boto_client=MagicMock())
        assert provider.infer_language_from_voice("seoyeon") == "ko"

    def test_unknown_voice_raises(self) -> None:
        import punt_tts.providers.polly as polly

        polly.VOICES.clear()
        polly._voices_loaded = False  # pyright: ignore[reportPrivateUsage]

        mock_client = MagicMock()
        mock_client.describe_voices.return_value = _make_describe_voices_response([])

        provider = PollyProvider(boto_client=mock_client)
        with pytest.raises(ValueError, match="Unknown voice"):
            provider.infer_language_from_voice("nonexistent")


class TestPollyProviderSynthesizeLanguage:
    def test_infers_language_from_voice(
        self,
        polly_provider: PollyProvider,
        tmp_output_dir: Path,
    ) -> None:
        request = SynthesisRequest(text="hello", voice="joanna")
        result = polly_provider.synthesize(request, tmp_output_dir / "test.mp3")
        assert result.language == "en"

    def test_explicit_language_preserved(
        self,
        polly_provider: PollyProvider,
        tmp_output_dir: Path,
    ) -> None:
        request = SynthesisRequest(text="hello", voice="joanna", language="en")
        result = polly_provider.synthesize(request, tmp_output_dir / "test.mp3")
        assert result.language == "en"

    def test_explicit_language_overrides_inference(
        self,
        polly_provider: PollyProvider,
        tmp_output_dir: Path,
    ) -> None:
        request = SynthesisRequest(text="Guten Tag", voice="hans", language="de")
        result = polly_provider.synthesize(request, tmp_output_dir / "test.mp3")
        assert result.language == "de"
