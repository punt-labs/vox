"""Tests for punt_vox.providers.transport_faults -- classifying transport failures."""

from __future__ import annotations

import ssl

import httpx
import pytest

from punt_vox.providers.ca_bundle import CaBundleError
from punt_vox.providers.transport_faults import ProviderTransportError, TransportFaults

_PATH = "/etc/private/host-ca.pem"


def _chained(depth: int) -> httpx.ConnectError:
    """Return an httpx error with a verify failure *depth* links down its chain."""
    root: BaseException = ssl.SSLCertVerificationError(1, "verify failed")
    for _ in range(depth - 1):
        link = httpx.ConnectError("httpcore")
        link.__cause__ = root
        root = link
    top = httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED]")
    top.__cause__ = root
    return top


@pytest.fixture
def bundle_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SSL_CERT_FILE", _PATH)


@pytest.fixture
def bundle_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)


@pytest.mark.usefixtures("bundle_set")
@pytest.mark.parametrize("depth", [1, 2, 3])
def test_verify_failure_with_bundle_set_is_a_bundle_error(depth: int) -> None:
    translated = TransportFaults.translate(_chained(depth))

    assert isinstance(translated, CaBundleError)
    assert "SSL_CERT_FILE" in str(translated)
    assert _PATH not in str(translated)


@pytest.mark.usefixtures("bundle_set")
def test_verify_failure_found_through_implicit_context() -> None:
    """An SDK that re-raises inside ``except`` links by ``__context__``."""
    top = httpx.ConnectError("wrapped")
    top.__context__ = ssl.SSLCertVerificationError(1, "verify failed")

    assert isinstance(TransportFaults.translate(top), CaBundleError)


@pytest.mark.usefixtures("bundle_unset")
def test_verify_failure_without_bundle_is_transient() -> None:
    """No SSL_CERT_FILE: the failure is not the operator's bundle to blame."""
    assert isinstance(TransportFaults.translate(_chained(2)), ProviderTransportError)


@pytest.mark.usefixtures("bundle_set")
def test_plain_connect_error_is_transient_even_with_bundle_set() -> None:
    translated = TransportFaults.translate(httpx.ConnectError("refused"))

    assert isinstance(translated, ProviderTransportError)
    assert "refused" in str(translated)


@pytest.mark.usefixtures("bundle_unset")
def test_transport_error_is_a_connection_error() -> None:
    """Callers already treating OSError as transient need no new case."""
    assert isinstance(
        TransportFaults.translate(httpx.ReadTimeout("slow")), ConnectionError
    )


@pytest.mark.usefixtures("bundle_set")
def test_cyclic_chain_terminates() -> None:
    first = httpx.ConnectError("a")
    second = httpx.ConnectError("b")
    first.__cause__ = second
    second.__cause__ = first

    assert isinstance(TransportFaults.translate(first), ProviderTransportError)
