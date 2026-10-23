"""Tests for punt_vox.providers.provider_faults -- the pre-answer failure types."""

from __future__ import annotations

from punt_vox.providers.ca_bundle import CaBundleError
from punt_vox.providers.provider_faults import (
    PERMANENT_ERRORS,
    TRANSPORT_ERRORS,
    ProviderConfigError,
    ProviderTransportError,
    UnsendableHeaderError,
)
from punt_vox.types_provider_errors import ProviderAuthError


def test_transport_error_is_a_connection_error() -> None:
    """Callers already treating OSError as transient need no new case."""
    assert issubclass(ProviderTransportError, ConnectionError)


def test_config_error_is_a_value_error() -> None:
    """ValueError: the wire reply routes it verbatim as a caller rejection."""
    assert issubclass(ProviderConfigError, ValueError)


def test_header_error_is_a_config_error() -> None:
    """Permanent everywhere a config error is -- music, STT, the wire."""
    assert issubclass(UnsendableHeaderError, ProviderConfigError)


def test_transport_errors_is_the_closed_set() -> None:
    assert set(TRANSPORT_ERRORS) == {
        CaBundleError,
        ProviderConfigError,
        ProviderTransportError,
    }


def test_permanent_errors_is_the_closed_set() -> None:
    """What no retry with the same host settings can fix: music stops filling
    and a call ends on these, and nothing else."""
    assert set(PERMANENT_ERRORS) == {
        ProviderAuthError,
        CaBundleError,
        ProviderConfigError,
    }


def test_no_transient_error_is_permanent() -> None:
    assert not issubclass(ProviderTransportError, PERMANENT_ERRORS)
