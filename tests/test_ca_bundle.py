"""Tests for punt_vox.providers.ca_bundle -- naming the trust anchors TLS used."""

from __future__ import annotations

import logging
import ssl
from typing import TYPE_CHECKING

import pytest

from punt_vox.providers.ca_bundle import CaBundle, CaBundleError

if TYPE_CHECKING:
    from collections.abc import Callable

_PATH = "/etc/private/host-ca.pem"
_DIR = "/etc/private/certs"


def _verify_error(
    strerror: str = "certificate verify failed", verify_message: str | None = None
) -> ssl.SSLCertVerificationError:
    """Return a verify failure shaped like the one ``ssl`` raises.

    The ``ssl`` module sets ``verify_message`` on the errors it raises; one built
    by hand has it only when the caller sets it.
    """
    error = ssl.SSLCertVerificationError(1, strerror)
    if verify_message is not None:
        error.verify_message = verify_message
    return error


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

        message = str(CaBundle.in_effect().untrusted(_verify_error()))

        assert "SSL_CERT_FILE" in message
        assert _PATH not in message

    def test_names_ssl_cert_dir_when_only_it_is_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SSL_CERT_FILE", raising=False)
        monkeypatch.setenv("SSL_CERT_DIR", _DIR)

        message = str(CaBundle.in_effect().untrusted(_verify_error()))

        assert "SSL_CERT_DIR" in message
        assert "unset SSL_CERT_DIR" in message
        assert _DIR not in message

    @pytest.mark.usefixtures("no_trust_vars")
    def test_names_the_default_bundle_when_neither_is_set(self) -> None:
        """No variable to blame: say whose trust failed and how to add a CA."""
        message = str(CaBundle.in_effect().untrusted(_verify_error()))

        assert "default CA bundle" in message
        assert "set SSL_CERT_FILE" in message


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
        CaBundle.in_effect().untrusted(_verify_error())

    assert [r.levelno for r in caplog.records] == [logging.ERROR]
