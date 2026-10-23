"""Tests for punt_vox.providers.api_key -- a key checked before it reaches a header."""

from __future__ import annotations

import traceback

import pytest

from punt_vox.providers.api_key import ApiKeyCheck, MalformedApiKeyError
from punt_vox.providers.caller_errors import CALLER_ERRORS
from punt_vox.providers.provider_faults import PERMANENT_ERRORS, ProviderConfigError
from punt_vox.types_provider_errors import ProviderUnavailableError

_KEY = "SECRETKEY123"

# Every shape a pasted key takes that httpx cannot put in a header: control
# characters, edge whitespace, and the non-ASCII characters a rich-text paste
# carries in -- httpx encodes header values as ASCII and raises a
# UnicodeEncodeError whose repr quotes the whole value.
_UNSENDABLE = {
    "newline": f"{_KEY}\n",
    "carriage-return": f"{_KEY}\r",
    "tab": f"\t{_KEY}",
    "del": f"{_KEY}\x7f",
    "nbsp": "SECRET\u00a0KEY123",
    "zero-width-space": f"{_KEY}\u200b",
    "en-dash": "SECRET\u2013KEY123",
    "smart-quote": f"sk-ab\u2019cd{_KEY}",
    "trailing-space": f"{_KEY} ",
    "leading-space": f" {_KEY}",
}
_UNSENDABLE_KEYS = list(_UNSENDABLE.values())
_UNSENDABLE_IDS = list(_UNSENDABLE)
_REASON = (
    "contains a character no HTTP header may carry "
    "(whitespace, a newline, or a non-ASCII character)"
)


@pytest.mark.parametrize("key", _UNSENDABLE_KEYS, ids=_UNSENDABLE_IDS)
def test_unsendable_key_is_rejected(key: str) -> None:
    with pytest.raises(MalformedApiKeyError):
        ApiKeyCheck("ELEVENLABS_API_KEY").require(key)


@pytest.mark.parametrize("key", [_KEY, "sk-proj-abc_DEF-123", "a b"])
def test_printable_key_passes_unchanged(key: str) -> None:
    assert ApiKeyCheck("OPENAI_API_KEY").require(key) == key


def test_absent_key_passes() -> None:
    """Absence is the readiness gate's verdict, not this check's."""
    assert ApiKeyCheck("OPENAI_API_KEY").require(None) is None


@pytest.mark.parametrize("key", _UNSENDABLE_KEYS, ids=_UNSENDABLE_IDS)
def test_message_names_the_source_never_the_key(key: str) -> None:
    with pytest.raises(MalformedApiKeyError) as info:
        ApiKeyCheck("ELEVENLABS_API_KEY").require(key)

    assert str(info.value) == f"ELEVENLABS_API_KEY {_REASON}"
    assert "SECRET" not in "".join(traceback.format_exception(info.value))
    assert "SECRET" not in repr(info.value)


def test_is_a_permanent_caller_error() -> None:
    """Routed verbatim on the wire, permanent for music and calls."""
    assert issubclass(MalformedApiKeyError, ProviderConfigError)
    assert issubclass(MalformedApiKeyError, CALLER_ERRORS)
    assert issubclass(MalformedApiKeyError, PERMANENT_ERRORS)


class TestRequireSet:
    """For a client that captured its key when built: no key is final.

    Given no key, an SDK falls back to the environment as it is at that
    moment -- which can hold another request's key.
    """

    _CHECK = ApiKeyCheck("OPENAI_API_KEY")

    def test_absent_key_is_the_readiness_gate_s_error(self) -> None:
        with pytest.raises(ProviderUnavailableError) as info:
            self._CHECK.require_set(None, "openai")

        assert info.value.provider_name == "openai"
        assert "OPENAI_API_KEY" in str(info.value)

    def test_good_key_is_returned(self) -> None:
        assert self._CHECK.require_set(_KEY, "openai") == _KEY

    @pytest.mark.parametrize("key", _UNSENDABLE_KEYS, ids=_UNSENDABLE_IDS)
    def test_unsendable_key_is_rejected(self, key: str) -> None:
        with pytest.raises(MalformedApiKeyError):
            self._CHECK.require_set(key, "openai")


class TestReport:
    """The one API-key health line every key-authenticated provider reports."""

    _CHECK = ApiKeyCheck("ELEVENLABS_API_KEY")

    @pytest.mark.parametrize("key", [None, ""])
    def test_absent_key_says_what_to_export(self, key: str | None) -> None:
        line = self._CHECK.report("ElevenLabs", key)

        assert not line.passed
        assert line.message == "ElevenLabs API key: not set (export ELEVENLABS_API_KEY)"

    @pytest.mark.parametrize("key", _UNSENDABLE_KEYS, ids=_UNSENDABLE_IDS)
    def test_malformed_key_fails_without_its_value(self, key: str) -> None:
        line = self._CHECK.report("ElevenLabs", key)

        assert not line.passed
        assert line.message == f"ElevenLabs API key: ELEVENLABS_API_KEY {_REASON}"
        assert "SECRET" not in line.message

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
