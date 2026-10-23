"""The CA certificates a provider's TLS trusts, and the ways verification breaks.

An SDK's HTTP client picks its trust anchors when it is constructed, not per
request: ``SSL_CERT_FILE`` if set, else ``SSL_CERT_DIR`` if set, else its own
default -- certifi's bundle for httpx (the ElevenLabs SDK), the system trust
store through ``truststore`` for httpx2 (the OpenAI SDK). A failure surfaces in
one of two places:

* **Build time.** ``SSL_CERT_FILE`` is missing or holds no certificate, and the
  SDK constructor raises a bare ``OSError`` that names neither the variable nor
  the path. (``SSL_CERT_DIR`` cannot fail here: OpenSSL reads it lazily.)
* **Request time.** The handshake fails with ``ssl.SSLCertVerificationError``
  buried in the SDK's exception chain -- because the anchors do not trust the
  certificate's issuer, or because the certificate names another host or is
  outside its validity period, which no change of anchors fixes.

Either way the operator needs to be told what failed. :class:`CaBundle` turns
each into a :class:`CaBundleError` whose message names the variable (or the
default anchors) and the fix -- but never the path, because the message crosses
the wire to every client, and the path is a host detail. The path is logged
once, at ERROR, where the failure is translated, so the operator finds it in
``vox.log``.

Provider-neutral: every SDK that rides on httpx or httpx2 fails this way, so
every provider routes its failures through this one module.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Final, Literal, Self, final

if TYPE_CHECKING:
    import ssl

logger = logging.getLogger(__name__)

__all__ = ["CaBundle", "CaBundleError", "TrustDefault"]

_CA_FILE_VAR: Final = "SSL_CERT_FILE"
_CA_DIR_VAR: Final = "SSL_CERT_DIR"

# What each variable names, in the order the HTTP clients consult them.
_NOUNS: Final = {_CA_FILE_VAR: "CA bundle", _CA_DIR_VAR: "CA directory"}

# Whose default anchors a client trusts when neither variable is set: httpx's
# (certifi) or httpx2's (the system store, through truststore).
type TrustDefault = Literal["certifi", "system"]
_DEFAULT_ANCHORS: Final[dict[TrustDefault, str]] = {
    "certifi": "the default CA bundle (certifi)",
    "system": "the system trust store (truststore)",
}

# OpenSSL's X509_V_ERR codes for the failures no change of trust anchors fixes,
# as ``ssl`` reports them in ``verify_code``.
_HOSTNAME_MISMATCH: Final = 62
_OUTSIDE_VALIDITY: Final = frozenset({9, 10})  # not yet valid, expired


class CaBundleError(ValueError):
    """TLS verification of the provider's certificate failed.

    A ``ValueError`` because it is a configuration fault, not a transient I/O
    one: retrying with the same environment fails the same way, and the wire
    reply routes a ``ValueError`` message to the caller verbatim.
    """


@final
class CaBundle:
    """The trust anchors one variable names, describing its own failures."""

    __slots__ = ("_default", "_path", "_var")
    _path: str
    # None: neither variable is set, so the client trusts its default anchors
    # -- no variable to name, and no path of ours to log.
    _var: str | None
    _default: TrustDefault

    def __new__(
        cls,
        path: str,
        *,
        var: str | None = _CA_FILE_VAR,
        default: TrustDefault = "certifi",
    ) -> Self:
        self = super().__new__(cls)
        self._path = path
        self._var = var
        self._default = default
        return self

    @classmethod
    def configured(cls) -> Self | None:  # None: SSL_CERT_FILE unset, see below
        """Return the bundle ``SSL_CERT_FILE`` names, or ``None`` when it is unset.

        The build-time question: only ``SSL_CERT_FILE`` is loaded when a client
        is constructed, so only it can fail the build. ``None`` is the contract,
        not a failure -- the build error is not ours to rename.
        """
        path = os.environ.get(_CA_FILE_VAR)
        return cls(path) if path else None

    @classmethod
    def in_effect(cls, *, default: TrustDefault) -> Self:
        """Return the trust anchors the client used: the file, else the dir,
        else *default* -- the anchors that client falls back to."""
        for var in _NOUNS:
            if path := os.environ.get(var):
                return cls(path, var=var, default=default)
        return cls("", var=None, default=default)

    def unloadable(self, cause: OSError) -> CaBundleError:
        """Describe a bundle that could not be loaded when the client was built."""
        # strerror, not str(cause): a FileNotFoundError's str carries the path.
        reason = cause.strerror or type(cause).__name__
        logger.error(
            "%s=%s is not a loadable CA bundle: %s", self._var, self._path, reason
        )
        return CaBundleError(
            f"{self._var} names a CA bundle that cannot be loaded ({reason}); "
            f"fix the path or unset {self._var}"
        )

    def untrusted(self, cause: ssl.SSLCertVerificationError) -> CaBundleError:
        """Describe a failed verification of the provider's certificate.

        Worded by OpenSSL's ``verify_code``: a certificate for another host, or
        one outside its validity period, is not the trust anchors' fault and
        gets its own fix; everything else -- an unknown or self-signed issuer
        -- is.
        """
        # None: a hand-built error without the attribute; treated as distrust.
        code: int | None = getattr(cause, "verify_code", None)
        reason = self._verify_reason(cause)
        if code == _HOSTNAME_MISMATCH:
            logger.error("provider certificate does not match the host: %s", reason)
            # No reason in the message: OpenSSL's names the host, which may be
            # an internal base URL.
            return CaBundleError(
                "the provider's TLS certificate does not match the host it was "
                "reached at; check the provider base URL and the proxy settings "
                "(HTTPS_PROXY, ALL_PROXY)"
            )
        if code in _OUTSIDE_VALIDITY:
            logger.error("provider certificate is outside its validity: %s", reason)
            return CaBundleError(
                f"the provider's TLS certificate is outside its validity period "
                f"({reason}); check this host's clock, or the server's certificate"
            )
        return self._distrusted(reason)

    def _distrusted(self, reason: str) -> CaBundleError:
        """Describe trust anchors that loaded but do not trust the issuer."""
        if self._var is None:
            anchors = _DEFAULT_ANCHORS[self._default]
            logger.error(
                "%s does not trust the provider's certificate: %s", anchors, reason
            )
            return CaBundleError(
                f"{anchors} does not trust the provider's certificate ({reason}); "
                f"behind a TLS-inspecting proxy, set {_CA_FILE_VAR} to a bundle "
                "that holds its CA"
            )
        noun = _NOUNS[self._var]
        logger.error(
            "%s=%s does not trust the provider's certificate: %s",
            self._var,
            self._path,
            reason,
        )
        return CaBundleError(
            f"{self._var} names a {noun} that does not trust the provider's "
            f"certificate ({reason}); fix the {noun} or unset {self._var}"
        )

    @staticmethod
    def _verify_reason(cause: ssl.SSLCertVerificationError) -> str:
        """Return OpenSSL's reason for the failure; it never carries the path.

        ``verify_message`` is the bare reason ("self-signed certificate in
        certificate chain"); ``strerror`` wraps it in the ``[SSL: ...]`` prefix
        and the C source location. The ``ssl`` module sets ``verify_message`` on
        every error it raises, but one built by hand need not have it.
        """
        # None: a hand-built error without the attribute, as above.
        verify_message: str | None = getattr(cause, "verify_message", None)
        return verify_message or cause.strerror or "certificate verify failed"
