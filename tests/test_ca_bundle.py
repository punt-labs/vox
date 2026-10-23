"""Tests for punt_vox.providers.ca_bundle -- naming the trust anchors TLS used."""

from __future__ import annotations

import logging
import ssl
from typing import TYPE_CHECKING, Literal

import pytest

from punt_vox.providers.ca_bundle import CaBundle, CaBundleError

if TYPE_CHECKING:
    from collections.abc import Callable

_PATH = "/etc/private/host-ca.pem"
_DIR = "/etc/private/certs"


def _verify_error(
    strerror: str = "certificate verify failed",
    verify_message: str | None = None,
    verify_code: int | None = None,
) -> ssl.SSLCertVerificationError:
    """Return a verify failure shaped like the one ``ssl`` raises.

    The ``ssl`` module sets ``verify_code`` and ``verify_message`` on the errors
    it raises; one built by hand has them only when the caller sets them.
    """
    error = ssl.SSLCertVerificationError(1, strerror)
    if verify_message is not None:
        error.verify_message = verify_message
    if verify_code is not None:
        error.verify_code = verify_code
    return error


# OpenSSL's X509_V_ERR codes, as ``ssl`` reports them in ``verify_code``.
_SELF_SIGNED_IN_CHAIN = 19
_UNABLE_TO_GET_ISSUER_LOCALLY = 20
_NOT_YET_VALID = 9
_EXPIRED = 10
_HOSTNAME_MISMATCH = 62


@pytest.fixture
def no_trust_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)


class TestConfigured:
    """``configured`` is the build-time question: is SSL_CERT_FILE set?"""

    @pytest.mark.usefixtures("no_trust_vars")
    def test_is_none_when_unset(self) -> None:
        assert CaBundle.configured() is None

    def test_is_none_when_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SSL_CERT_FILE", "")

        assert CaBundle.configured() is None

    def test_reads_the_variable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SSL_CERT_FILE", _PATH)

        assert CaBundle.configured() is not None

    def test_ignores_ssl_cert_dir(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A bad SSL_CERT_DIR never fails the build -- OpenSSL reads it lazily."""
        monkeypatch.delenv("SSL_CERT_FILE", raising=False)
        monkeypatch.setenv("SSL_CERT_DIR", _DIR)

        assert CaBundle.configured() is None


class TestInEffect:
    """``in_effect`` names what httpx trusted: the file, else the dir, else certifi."""

    def test_names_ssl_cert_file_over_ssl_cert_dir(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SSL_CERT_FILE", _PATH)
        monkeypatch.setenv("SSL_CERT_DIR", _DIR)

        message = str(CaBundle.in_effect(default="certifi").untrusted(_verify_error()))

        assert "SSL_CERT_FILE" in message
        assert _PATH not in message

    def test_names_ssl_cert_dir_when_only_it_is_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SSL_CERT_FILE", raising=False)
        monkeypatch.setenv("SSL_CERT_DIR", _DIR)

        message = str(CaBundle.in_effect(default="certifi").untrusted(_verify_error()))

        assert "SSL_CERT_DIR" in message
        assert "unset SSL_CERT_DIR" in message
        assert _DIR not in message

    @pytest.mark.usefixtures("no_trust_vars")
    def test_names_the_default_bundle_when_neither_is_set(self) -> None:
        """No variable to blame: say whose trust failed and how to add a CA."""
        message = str(CaBundle.in_effect(default="certifi").untrusted(_verify_error()))

        assert "default CA bundle (certifi)" in message
        assert "set SSL_CERT_FILE" in message

    @pytest.mark.usefixtures("no_trust_vars")
    def test_names_the_system_trust_store_for_an_sdk_that_uses_it(self) -> None:
        """httpx2 -- the OpenAI SDK's -- trusts the system store, not certifi."""
        message = str(CaBundle.in_effect(default="system").untrusted(_verify_error()))

        assert "system trust store" in message
        assert "(certifi)" not in message
        assert "set SSL_CERT_FILE" in message


class TestVerifyFailureKind:
    """Only an untrusted issuer is the trust anchors' fault; say what else failed."""

    @pytest.mark.usefixtures("no_trust_vars")
    @pytest.mark.parametrize(
        "code", [_SELF_SIGNED_IN_CHAIN, _UNABLE_TO_GET_ISSUER_LOCALLY, None]
    )
    @pytest.mark.parametrize("default", ["certifi", "system"])
    def test_untrusted_issuer_blames_the_trust_anchors(
        self, code: int | None, default: Literal["certifi", "system"]
    ) -> None:
        cause = _verify_error(verify_code=code)

        message = str(CaBundle.in_effect(default=default).untrusted(cause))

        assert "does not trust the provider's certificate" in message

    @pytest.mark.parametrize("set_bundle", [False, True])
    def test_hostname_mismatch_points_at_the_base_url_and_proxy(
        self, set_bundle: bool, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A bundle cannot fix a certificate issued for another host."""
        monkeypatch.delenv("SSL_CERT_DIR", raising=False)
        if set_bundle:
            monkeypatch.setenv("SSL_CERT_FILE", _PATH)
        else:
            monkeypatch.delenv("SSL_CERT_FILE", raising=False)
        cause = _verify_error(
            verify_message="Hostname mismatch, certificate is not valid for "
            "'internal.example'.",
            verify_code=_HOSTNAME_MISMATCH,
        )

        error = CaBundle.in_effect(default="certifi").untrusted(cause)

        assert isinstance(error, CaBundleError)
        assert "does not match" in str(error)
        assert "base URL" in str(error)
        assert "SSL_CERT_FILE" not in str(error)
        assert "internal.example" not in str(error)

    @pytest.mark.usefixtures("no_trust_vars")
    @pytest.mark.parametrize("code", [_EXPIRED, _NOT_YET_VALID])
    def test_validity_failure_points_at_the_clock_or_the_server(
        self, code: int
    ) -> None:
        cause = _verify_error(
            verify_message="certificate has expired", verify_code=code
        )

        error = CaBundle.in_effect(default="system").untrusted(cause)

        assert isinstance(error, CaBundleError)
        assert "validity period" in str(error)
        assert "clock" in str(error)
        assert "trust store" not in str(error)

    @pytest.mark.usefixtures("no_trust_vars")
    def test_hostname_mismatch_is_logged_with_its_reason(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        cause = _verify_error(
            verify_message="Hostname mismatch for 'internal.example'.",
            verify_code=_HOSTNAME_MISMATCH,
        )

        with caplog.at_level(logging.ERROR, logger="punt_vox.providers.ca_bundle"):
            CaBundle.in_effect(default="certifi").untrusted(cause)

        assert "internal.example" in caplog.text


def test_bundle_error_is_a_value_error() -> None:
    """ValueError routes verbatim through the wire reply as a rejection."""
    error = CaBundle(_PATH).unloadable(FileNotFoundError(2, "No such file", _PATH))

    assert isinstance(error, ValueError)


@pytest.mark.parametrize(
    "error",
    [
        CaBundle(_PATH).unloadable(FileNotFoundError(2, "No such file", _PATH)),
        CaBundle(_PATH).untrusted(_verify_error()),
    ],
    ids=["unloadable", "untrusted"],
)
def test_message_names_the_variable_not_the_path(error: CaBundleError) -> None:
    assert "SSL_CERT_FILE" in str(error)
    assert _PATH not in str(error)


def test_unloadable_carries_the_reason() -> None:
    error = CaBundle(_PATH).unloadable(FileNotFoundError(2, "No such file", _PATH))

    assert "No such file" in str(error)


def test_unloadable_without_strerror_names_the_error_type() -> None:
    """A bare OSError's str may carry the path; the type name never does."""
    error = CaBundle(_PATH).unloadable(OSError(_PATH))

    assert "OSError" in str(error)
    assert _PATH not in str(error)


def test_untrusted_prefers_the_verify_message() -> None:
    """``verify_message`` is OpenSSL's reason alone, without the bracketed prefix."""
    cause = _verify_error(
        strerror="[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
        "self-signed certificate in certificate chain (_ssl.c:1032)",
        verify_message="self-signed certificate in certificate chain",
    )

    message = str(CaBundle(_PATH).untrusted(cause))

    assert "(self-signed certificate in certificate chain)" in message
    assert "_ssl.c" not in message


def test_untrusted_falls_back_to_strerror_without_a_verify_message() -> None:
    cause = _verify_error(strerror="certificate verify failed: unable to get issuer")

    assert "unable to get issuer" in str(CaBundle(_PATH).untrusted(cause))


def _unloadable(bundle: CaBundle) -> CaBundleError:
    return bundle.unloadable(FileNotFoundError(2, "No such file", _PATH))


def _untrusted(bundle: CaBundle) -> CaBundleError:
    return bundle.untrusted(_verify_error())


@pytest.mark.parametrize(
    "translate", [_unloadable, _untrusted], ids=["unloadable", "untrusted"]
)
def test_logs_the_path_once_at_error(
    translate: Callable[[CaBundle], CaBundleError], caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR, logger="punt_vox.providers.ca_bundle"):
        translate(CaBundle(_PATH))

    assert [r.levelno for r in caplog.records if _PATH in r.getMessage()] == [
        logging.ERROR
    ]


@pytest.mark.usefixtures("no_trust_vars")
def test_default_bundle_failure_is_logged_at_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.ERROR, logger="punt_vox.providers.ca_bundle"):
        CaBundle.in_effect(default="certifi").untrusted(_verify_error())

    assert [r.levelno for r in caplog.records] == [logging.ERROR]
