"""Tests for punt_vox.providers.api_key -- a key checked before it reaches a header."""

from __future__ import annotations

import traceback

import pytest

from punt_vox.providers.api_key import ApiKeyCheck, MalformedApiKeyError
from punt_vox.providers.caller_errors import CALLER_ERRORS
from punt_vox.providers.provider_faults import ProviderConfigError

_KEY = "SECRETKEY123"


@pytest.mark.parametrize("key", [f"{_KEY}\n", f"{_KEY}\r", f"\t{_KEY}", f"{_KEY}\x7f"])
def test_control_character_is_rejected(key: str) -> None:
    with pytest.raises(MalformedApiKeyError):
        ApiKeyCheck("ELEVENLABS_API_KEY").require(key)


@pytest.mark.parametrize("key", [_KEY, "sk-proj-abc_DEF-123", "a b"])
def test_printable_key_passes_unchanged(key: str) -> None:
    assert ApiKeyCheck("OPENAI_API_KEY").require(key) == key


def test_absent_key_passes() -> None:
    """Absence is the readiness gate's verdict, not this check's."""
    assert ApiKeyCheck("OPENAI_API_KEY").require(None) is None


def test_message_names_the_source_never_the_key() -> None:
    with pytest.raises(MalformedApiKeyError) as info:
        ApiKeyCheck("ELEVENLABS_API_KEY").require(f"{_KEY}\n")

    assert "ELEVENLABS_API_KEY" in str(info.value)
    assert "stray whitespace or newlines" in str(info.value)
    assert _KEY not in "".join(traceback.format_exception(info.value))


def test_is_a_permanent_caller_error() -> None:
    """Routed verbatim on the wire, permanent for music and calls."""
    assert issubclass(MalformedApiKeyError, ProviderConfigError)
    assert issubclass(MalformedApiKeyError, CALLER_ERRORS)


class TestReport:
    """The one API-key health line every key-authenticated provider reports."""

    _CHECK = ApiKeyCheck("ELEVENLABS_API_KEY")

    @pytest.mark.parametrize("key", [None, ""])
    def test_absent_key_says_what_to_export(self, key: str | None) -> None:
        line = self._CHECK.report("ElevenLabs", key)

        assert not line.passed
        assert line.message == "ElevenLabs API key: not set (export ELEVENLABS_API_KEY)"

    def test_malformed_key_fails_without_its_value(self) -> None:
        line = self._CHECK.report("ElevenLabs", f"{_KEY}\n")

        assert not line.passed
        assert "control character" in line.message
        assert _KEY not in line.message

    def test_good_key_passes(self) -> None:
        line = self._CHECK.report("ElevenLabs", _KEY)

        assert line.passed
        assert line.message == "ElevenLabs API key: set"


@pytest.mark.parametrize(
    ("provider", "var"),
    [("elevenlabs", "ELEVENLABS_API_KEY"), ("openai", "OPENAI_API_KEY")],
)
def test_for_provider_reads_the_gate_s_variable(provider: str, var: str) -> None:
    assert ApiKeyCheck.for_provider(provider).source == var
