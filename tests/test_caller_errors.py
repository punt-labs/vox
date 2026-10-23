"""Tests for punt_vox.providers.caller_errors -- the closed verbatim set."""

from __future__ import annotations

import pytest

from punt_vox.providers.ca_bundle import CaBundleError
from punt_vox.providers.caller_errors import CALLER_ERRORS
from punt_vox.providers.provider_faults import ProviderConfigError
from punt_vox.types_errors import VoiceNotFoundError
from punt_vox.types_provider_errors import (
    ProviderAuthError,
    ProviderUnavailableError,
    UnknownProviderError,
)


def test_the_set_is_exactly_the_diagnosable_provider_failures() -> None:
    assert set(CALLER_ERRORS) == {
        ProviderUnavailableError,
        ProviderAuthError,
        UnknownProviderError,
        VoiceNotFoundError,
        CaBundleError,
        ProviderConfigError,
    }


@pytest.mark.parametrize("error", [ValueError("bug"), RuntimeError("bug")])
def test_a_generic_error_is_not_a_caller_error(error: Exception) -> None:
    """Widening to every ValueError would report daemon bugs as rejections."""
    assert not isinstance(error, CALLER_ERRORS)


def test_a_ca_bundle_error_is_a_caller_error() -> None:
    assert isinstance(CaBundleError("SSL_CERT_FILE ..."), CALLER_ERRORS)


def test_a_provider_config_error_is_a_caller_error() -> None:
    """A proxy or base URL httpx refuses is host configuration, like the bundle."""
    assert isinstance(ProviderConfigError("provider request ..."), CALLER_ERRORS)
