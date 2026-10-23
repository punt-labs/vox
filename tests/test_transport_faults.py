"""Tests for punt_vox.providers.transport_faults -- classifying transport failures."""

from __future__ import annotations

import logging
import ssl

import httpx
import httpx2
import pytest

from punt_vox.providers.ca_bundle import CaBundleError
from punt_vox.providers.provider_faults import (
    ProviderConfigError,
    ProviderTransportError,
    UnsendableHeaderError,
)
from punt_vox.providers.transport_faults import TransportFaults

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
        httpx.ProxyError("407 Proxy Authentication Required"),
        httpx.LocalProtocolError("Illegal header value b'key\\n'"),
        httpx2.UnsupportedProtocol("Request URL has an unsupported protocol 'ftp'"),
        httpx2.ProxyError("407 Proxy Authentication Required"),
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
        """A URL can carry credentials; the message crosses the wire."""
        fault = httpx.UnsupportedProtocol("unsupported 'ftp://user:s3cret@host/'")

        message = str(TransportFaults.translate(fault))

        assert "UnsupportedProtocol" in message
        assert "s3cret" not in message


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


class TestProxyFault:
    """A proxy that refuses credentials is configuration; one that fails is not.

    The texts are httpcore's own: an HTTP proxy's CONNECT reply is
    ``"<status> <reason>"``, a SOCKS proxy's refusals are fixed sentences.
    """

    _REFUSED = (
        "407 Proxy Authentication Required",
        "403 Forbidden",
        "Invalid username/password",
        (
            "Requested NO AUTHENTICATION REQUIRED from proxy server, but got "
            "USERNAME/PASSWORD."
        ),
    )
    _FAILING = (
        "503 Service Unavailable",
        "502 Bad Gateway",
        "Proxy Server could not connect: 5.",
    )

    @pytest.mark.usefixtures("bundle_unset")
    @pytest.mark.parametrize("text", _REFUSED)
    @pytest.mark.parametrize("kind", [httpx.ProxyError, httpx2.ProxyError])
    def test_credential_refusal_is_permanent(
        self, kind: type[httpx.ProxyError | httpx2.ProxyError], text: str
    ) -> None:
        translated = TransportFaults.translate(_wrapped(kind(text)))

        assert isinstance(translated, ProviderConfigError)
        assert "HTTPS_PROXY" in str(translated)

    @pytest.mark.usefixtures("bundle_unset")
    @pytest.mark.parametrize("text", _FAILING)
    @pytest.mark.parametrize("kind", [httpx.ProxyError, httpx2.ProxyError])
    def test_proxy_failure_is_transient(
        self, kind: type[httpx.ProxyError | httpx2.ProxyError], text: str
    ) -> None:
        translated = TransportFaults.translate(_wrapped(kind(text)))

        assert isinstance(translated, ProviderTransportError)
        assert text in str(translated)

    @pytest.mark.usefixtures("bundle_unset")
    def test_transient_proxy_message_points_at_the_proxy(self) -> None:
        """Not "check the network": the network to the proxy worked."""
        message = str(TransportFaults.translate(httpx.ProxyError("503 Unavailable")))

        assert "proxy" in message
        assert "HTTPS_PROXY" in message

    @pytest.mark.usefixtures("bundle_unset")
    def test_httpcore_link_beneath_httpx_is_classified_once(self) -> None:
        """httpx re-raises httpcore's ProxyError; the inner text is the same."""
        outer = httpx.ProxyError("503 Service Unavailable")
        outer.__cause__ = Exception("503 Service Unavailable")

        assert isinstance(TransportFaults.translate(outer), ProviderTransportError)


class TestHeaderFault:
    """A header httpx will not send quotes its value -- the API key."""

    _KEY = "SECRETKEY123"

    def _fault(
        self, kind: type[httpx.LocalProtocolError | httpx2.LocalProtocolError]
    ) -> Exception:
        return _wrapped(kind(f"Illegal header value b'Bearer {self._KEY}\\n'"))

    @pytest.mark.parametrize(
        "kind", [httpx.LocalProtocolError, httpx2.LocalProtocolError]
    )
    def test_is_permanent_and_names_the_api_key(
        self, kind: type[httpx.LocalProtocolError | httpx2.LocalProtocolError]
    ) -> None:
        translated = TransportFaults.translate(self._fault(kind))

        assert isinstance(translated, UnsendableHeaderError)
        assert isinstance(translated, ProviderConfigError)
        assert "stray whitespace or newlines" in str(translated)

    @pytest.mark.parametrize(
        "kind", [httpx.LocalProtocolError, httpx2.LocalProtocolError]
    )
    def test_never_logs_the_header_value(
        self,
        kind: type[httpx.LocalProtocolError | httpx2.LocalProtocolError],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        with caplog.at_level(logging.DEBUG):
            translated = TransportFaults.translate(self._fault(kind))

        assert caplog.records
        assert all(self._KEY not in r.getMessage() for r in caplog.records)
        assert all(r.exc_info is None for r in caplog.records)
        assert self._KEY not in str(translated)
