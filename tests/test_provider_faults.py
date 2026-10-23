"""Tests for punt_vox.providers.provider_faults -- the pre-answer failure types."""

from __future__ import annotations

from punt_vox.providers.ca_bundle import CaBundleError
from punt_vox.providers.provider_faults import (
    TRANSPORT_ERRORS,
    ProviderConfigError,
    ProviderTransportError,
    UnsendableHeaderError,
)


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
