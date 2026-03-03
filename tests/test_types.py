"""Tests for punt_vox.types."""

from __future__ import annotations

from pathlib import Path

import pytest

from punt_vox.types import (
    SUPPORTED_LANGUAGES,
    AudioProviderId,
    HealthCheck,
    MergeStrategy,
    SynthesisRequest,
    SynthesisResult,
    generate_filename,
    result_to_dict,
    validate_language,
)


class TestHealthCheck:
    def test_defaults_to_required(self) -> None:
        check = HealthCheck(passed=True, message="ok")
        assert check.required is True

    def test_optional_check(self) -> None:
        check = HealthCheck(passed=False, message="fail", required=False)
        assert check.required is False

    def test_frozen(self) -> None:
        check = HealthCheck(passed=True, message="ok")
        with pytest.raises(AttributeError):
            check.passed = False  # type: ignore[misc]


class TestMergeStrategy:
    def test_separate_value(self) -> None:
        assert MergeStrategy.ONE_FILE_PER_INPUT.value == "separate"

    def test_single_value(self) -> None:
        assert MergeStrategy.ONE_FILE_PER_BATCH.value == "single"


class TestSynthesisRequest:
    def test_default_rate(self) -> None:
        req = SynthesisRequest(text="hello", voice="joanna")
        assert req.rate is None

    def test_custom_rate(self) -> None:
        req = SynthesisRequest(text="hello", voice="joanna", rate=100)
        assert req.rate == 100

    def test_frozen(self) -> None:
        req = SynthesisRequest(text="hello", voice="joanna")
        with pytest.raises(AttributeError):
            req.text = "world"  # type: ignore[misc]

    def test_voice_is_string(self) -> None:
        req = SynthesisRequest(text="hello", voice="hans")
        assert req.voice == "hans"

    def test_language_default_none(self) -> None:
        req = SynthesisRequest(text="hello", voice="joanna")
        assert req.language is None

    def test_language_set(self) -> None:
        req = SynthesisRequest(text="Guten Tag", voice="daniel", language="de")
        assert req.language == "de"


class TestSynthesisResult:
    def test_to_dict(self) -> None:
        result = SynthesisResult(
            path=Path("/tmp/test.mp3"),
            text="hello",
            provider=AudioProviderId.openai,
            voice="Joanna",
        )
        d = result_to_dict(result)
        assert d["path"] == "/tmp/test.mp3"
        assert d["text"] == "hello"
        assert d["voice"] == "Joanna"
        assert "language" not in d

    def test_to_dict_with_language(self) -> None:
        result = SynthesisResult(
            path=Path("/tmp/test.mp3"),
            text="Guten Tag",
            provider=AudioProviderId.openai,
            voice="Daniel",
            language="de",
        )
        d = result_to_dict(result)
        assert d["language"] == "de"

    def test_language_default_none(self) -> None:
        result = SynthesisResult(
            path=Path("/tmp/test.mp3"),
            text="hello",
            provider=AudioProviderId.openai,
            voice="Joanna",
        )
        assert result.language is None


class TestValidateLanguage:
    def test_valid_code(self) -> None:
        assert validate_language("de") == "de"

    def test_normalizes_case(self) -> None:
        assert validate_language("DE") == "de"

    def test_strips_whitespace(self) -> None:
        assert validate_language(" fr ") == "fr"

    def test_rejects_too_long(self) -> None:
        with pytest.raises(ValueError, match="Invalid language code"):
            validate_language("deu")

    def test_rejects_too_short(self) -> None:
        with pytest.raises(ValueError, match="Invalid language code"):
            validate_language("d")

    def test_rejects_empty(self) -> None:
        with pytest.raises(ValueError, match="Invalid language code"):
            validate_language("")

    def test_rejects_digits(self) -> None:
        with pytest.raises(ValueError, match="Invalid language code"):
            validate_language("d1")

    def test_rejects_non_ascii(self) -> None:
        with pytest.raises(ValueError, match="Invalid language code"):
            validate_language("dé")


class TestSupportedLanguages:
    def test_has_common_languages(self) -> None:
        for code in ("de", "en", "es", "fr", "ja", "ko", "ru", "zh"):
            assert code in SUPPORTED_LANGUAGES

    def test_values_are_strings(self) -> None:
        for name in SUPPORTED_LANGUAGES.values():
            assert isinstance(name, str)
            assert len(name) > 0


class TestGenerateFilename:
    def test_deterministic(self) -> None:
        name1 = generate_filename("hello")
        name2 = generate_filename("hello")
        assert name1 == name2

    def test_different_text_different_name(self) -> None:
        name1 = generate_filename("hello")
        name2 = generate_filename("world")
        assert name1 != name2

    def test_ends_with_mp3(self) -> None:
        name = generate_filename("test")
        assert name.endswith(".mp3")

    def test_prefix(self) -> None:
        name = generate_filename("test", prefix="pair_")
        assert name.startswith("pair_")
        assert name.endswith(".mp3")

    def test_no_prefix(self) -> None:
        name = generate_filename("test")
        assert not name.startswith("pair_")
