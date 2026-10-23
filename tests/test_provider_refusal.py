"""Tests for punt_vox.providers.provider_refusal -- a refusal with its reason."""

from __future__ import annotations

import pytest

from punt_vox.providers.caller_errors import CALLER_ERRORS
from punt_vox.providers.provider_refusal import ProviderRefusalError
from punt_vox.types_provider_errors import ProviderAuthError


def test_is_a_provider_auth_error() -> None:
    """Routed and treated like any rejected credential."""
    assert issubclass(ProviderRefusalError, ProviderAuthError)
    assert issubclass(ProviderRefusalError, CALLER_ERRORS)


def test_message_carries_the_code() -> None:
    error = ProviderRefusalError.from_code("openai", 403, "model_not_allowed")

    assert str(error) == (
        "provider 'openai' refused the request (HTTP 403: model_not_allowed); "
        "run `vox doctor`"
    )
    assert error.provider_name == "openai"
    assert error.status_code == 403
    assert error.reason == "model_not_allowed"


@pytest.mark.parametrize("code", [None, "", "a b", "line\nforged", "é", "x" * 65])
def test_unusable_code_falls_back_to_the_plain_rejection(code: str | None) -> None:
    error = ProviderRefusalError.from_code("openai", 403, code)

    assert error.reason is None
    assert str(error) == (
        "provider 'openai' rejected the credentials (HTTP 403); run `vox doctor`"
    )
