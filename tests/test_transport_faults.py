"""Tests for punt_vox.providers.transport_faults -- classifying transport failures."""

from __future__ import annotations

import ssl

import httpx
import httpx2
import pytest

from punt_vox.providers.ca_bundle import CaBundleError
from punt_vox.providers.transport_faults import (
    TRANSPORT_ERRORS,
    ProviderConfigError,
    ProviderTransportError,
    TransportFaults,
)

_PATH = "/etc/private/host-ca.pem"


class _SdkConnectionError(Exception):
    """An SDK's own wrapper -- the shape openai.APIConnectionError has."""


def _wrapped(cause: BaseException, message: str = "Connection error.") -> Exception:
    """Return *cause* wrapped by an SDK error, linked the way ``raise from`` links."""
    wrapper = _SdkConnectionError(message)
    wrapper.__cause__ = cause
    return wrapper


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
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)


class TestVerifyFailure:
    """A certificate the trust anchors reject is configuration: permanent."""

    @pytest.mark.usefixtures("bundle_set")
    @pytest.mark.parametrize("depth", [1, 2, 3])
    def test_with_bundle_set_names_ssl_cert_file(self, depth: int) -> None:
        translated = TransportFaults.translate(_chained(depth))

        assert isinstance(translated, CaBundleError)
        assert "SSL_CERT_FILE" in str(translated)
        assert _PATH not in str(translated)

    @pytest.mark.usefixtures("bundle_set")
    def test_found_through_implicit_context(self) -> None:
        """An SDK that re-raises inside ``except`` links by ``__context__``."""
        top = httpx.ConnectError("wrapped")
        top.__context__ = ssl.SSLCertVerificationError(1, "verify failed")

        assert isinstance(TransportFaults.translate(top), CaBundleError)

    @pytest.mark.usefixtures("bundle_unset")
    def test_without_bundle_is_still_permanent(self) -> None:
        """No SSL_CERT_FILE: the default bundle failed, and a retry fails the same."""
        translated = TransportFaults.translate(_chained(2))

        assert isinstance(translated, CaBundleError)
        assert not isinstance(translated, ConnectionError)

    @pytest.mark.usefixtures("bundle_unset")
    def test_without_bundle_names_the_default_bundle(self) -> None:
        assert "default CA bundle" in str(TransportFaults.translate(_chained(2)))

    def test_with_only_ssl_cert_dir_names_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SSL_CERT_FILE", raising=False)
        monkeypatch.setenv("SSL_CERT_DIR", "/etc/private/certs")

        assert "SSL_CERT_DIR" in str(TransportFaults.translate(_chained(2)))

    @pytest.mark.usefixtures("bundle_unset")
    def test_found_beneath_an_sdk_wrapper(self) -> None:
        """OpenAI wraps httpx's error in its own; the verify failure is below both."""
        translated = TransportFaults.translate(_wrapped(_chained(2)))

        assert isinstance(translated, CaBundleError)


class TestConfigurationFault:
    """A request httpx refuses to send is configuration, not the network."""

    # httpx's and httpx2's: the ElevenLabs SDK rides on the first, OpenAI's on
    # the fork, and the two packages' error classes are unrelated types.
    _FAULTS = (
        httpx.UnsupportedProtocol("Request URL has an unsupported protocol 'ftp://'"),
        httpx.ProxyError("http://user:s3cret@proxy.internal:3128 refused"),
        httpx.LocalProtocolError("Illegal header value b'key\\n'"),
        httpx2.UnsupportedProtocol("Request URL has an unsupported protocol 'ftp'"),
        httpx2.ProxyError("http://user:s3cret@proxy.internal:3128 refused"),
        httpx2.LocalProtocolError("Illegal header value b'key\\n'"),
    )
    _IDS = (
        "unsupported-protocol",
        "proxy",
        "local-protocol",
        "httpx2-unsupported-protocol",
        "httpx2-proxy",
        "httpx2-local-protocol",
    )

    @pytest.mark.usefixtures("bundle_unset")
    @pytest.mark.parametrize("fault", _FAULTS, ids=_IDS)
    def test_is_a_permanent_config_error(
        self, fault: httpx.RequestError | httpx2.RequestError
    ) -> None:
        translated = TransportFaults.translate(fault)

        assert isinstance(translated, ProviderConfigError)
        assert not isinstance(translated, ConnectionError)

    @pytest.mark.usefixtures("bundle_unset")
    @pytest.mark.parametrize("fault", _FAULTS, ids=_IDS)
    def test_found_beneath_an_sdk_wrapper(
        self, fault: httpx.RequestError | httpx2.RequestError
    ) -> None:
        assert isinstance(
            TransportFaults.translate(_wrapped(fault)), ProviderConfigError
        )

    @pytest.mark.usefixtures("bundle_unset")
    def test_message_names_the_fault_type_not_its_text(self) -> None:
        """A proxy URL can carry credentials; the message crosses the wire."""
        message = str(TransportFaults.translate(self._FAULTS[1]))

        assert "ProxyError" in message
        assert "s3cret" not in message

    def test_config_error_is_a_value_error(self) -> None:
        """ValueError: the wire reply routes it verbatim as a caller rejection."""
        assert issubclass(ProviderConfigError, ValueError)


class TestTransientFailure:
    """Everything else is the network, and a retry may succeed."""

    @pytest.mark.usefixtures("bundle_set")
    def test_plain_connect_error_is_transient_even_with_bundle_set(self) -> None:
        translated = TransportFaults.translate(httpx.ConnectError("refused"))

        assert isinstance(translated, ProviderTransportError)
        assert "refused" in str(translated)

    @pytest.mark.usefixtures("bundle_unset")
    def test_is_a_connection_error(self) -> None:
        """Callers already treating OSError as transient need no new case."""
        assert isinstance(
            TransportFaults.translate(httpx.ReadTimeout("slow")), ConnectionError
        )

    @pytest.mark.usefixtures("bundle_set")
    def test_cyclic_chain_terminates(self) -> None:
        first = httpx.ConnectError("a")
        second = httpx.ConnectError("b")
        first.__cause__ = second
        second.__cause__ = first

        assert isinstance(TransportFaults.translate(first), ProviderTransportError)

    @pytest.mark.usefixtures("bundle_unset")
    def test_message_points_at_the_network(self) -> None:
        assert "check the network" in str(
            TransportFaults.translate(httpx.ConnectError("refused"))
        )

    @pytest.mark.usefixtures("bundle_unset")
    def test_message_names_the_innermost_cause_not_the_wrapper(self) -> None:
        """The SDK's "Connection error." says nothing; the refused socket does."""
        connect = httpx.ConnectError("httpx wrapper text")
        connect.__context__ = ConnectionRefusedError(111, "Connection refused")

        message = str(TransportFaults.translate(_wrapped(connect)))

        assert "ConnectionRefusedError" in message
        assert "Connection refused" in message
        assert "Connection error." not in message


def test_transport_errors_is_every_error_translate_returns() -> None:
    """The tuple a health check catches is exactly what a request can raise."""
    assert set(TRANSPORT_ERRORS) == {
        CaBundleError,
        ProviderConfigError,
        ProviderTransportError,
    }
