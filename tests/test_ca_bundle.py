"""Tests for punt_vox.providers.ca_bundle -- naming a bad ``SSL_CERT_FILE``."""

from __future__ import annotations

import logging
import ssl
from typing import TYPE_CHECKING

import pytest

from punt_vox.providers.ca_bundle import CaBundle, CaBundleError

if TYPE_CHECKING:
    from collections.abc import Callable

_PATH = "/etc/private/host-ca.pem"


def test_configured_is_none_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)

    assert CaBundle.configured() is None


def test_configured_is_none_when_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SSL_CERT_FILE", "")

    assert CaBundle.configured() is None


def test_configured_reads_the_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SSL_CERT_FILE", _PATH)

    assert CaBundle.configured() is not None


def test_bundle_error_is_a_value_error() -> None:
    """ValueError routes verbatim through the wire reply as a rejection."""
    error = CaBundle(_PATH).unloadable(FileNotFoundError(2, "No such file", _PATH))

    assert isinstance(error, ValueError)


@pytest.mark.parametrize(
    "error",
    [
        CaBundle(_PATH).unloadable(FileNotFoundError(2, "No such file", _PATH)),
        CaBundle(_PATH).untrusted(ssl.SSLCertVerificationError(1, "verify failed")),
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


def test_untrusted_carries_the_verify_message() -> None:
    cause = ssl.SSLCertVerificationError(
        1, "certificate verify failed: self-signed certificate in certificate chain"
    )

    error = CaBundle(_PATH).untrusted(cause)

    assert "self-signed certificate" in str(error)


def _unloadable(bundle: CaBundle) -> CaBundleError:
    return bundle.unloadable(FileNotFoundError(2, "No such file", _PATH))


def _untrusted(bundle: CaBundle) -> CaBundleError:
    return bundle.untrusted(ssl.SSLCertVerificationError(1, "verify failed"))


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
