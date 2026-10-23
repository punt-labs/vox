"""The CA bundle ``SSL_CERT_FILE`` names, and the two ways it breaks a provider.

httpx reads ``SSL_CERT_FILE`` when an SDK client is constructed, not per
request, so a bad value surfaces in one of two places:

* **Build time.** The path is missing or holds no certificate, and the SDK
  constructor raises a bare ``OSError`` that names neither the variable nor
  the path.
* **Request time.** The bundle loads but does not trust the provider's
  certificate, and the request fails with ``ssl.SSLCertVerificationError``
  buried in the SDK's exception chain.

Either way the operator who set the variable needs to be told it is the
cause. :class:`CaBundle` turns both into a :class:`CaBundleError` whose message
names the variable and the reason -- but never the path, because the message
crosses the wire to every client, and the path is a host detail. The path is
logged once, at ERROR, where the failure is translated, so the operator finds
it in ``vox.log``.

Provider-neutral: every SDK that rides on httpx fails this way, so every
provider routes its failures through this one module rather than its own copy.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Self, final

if TYPE_CHECKING:
    import ssl

logger = logging.getLogger(__name__)

__all__ = ["CaBundle", "CaBundleError"]

_CA_BUNDLE_VAR = "SSL_CERT_FILE"


class CaBundleError(ValueError):
    """``SSL_CERT_FILE`` names a CA bundle the provider's TLS cannot use.

    A ``ValueError`` because it is a bad configuration value, not a transient
    I/O fault: retrying with the same environment fails the same way, and the
    wire reply routes a ``ValueError`` message to the caller verbatim.
    """


@final
class CaBundle:
    """The CA bundle ``SSL_CERT_FILE`` points at, describing its own failures."""

    __slots__ = ("_path",)
    _path: str

    def __new__(cls, path: str) -> Self:
        self = super().__new__(cls)
        self._path = path
        return self

    @classmethod
    def configured(cls) -> Self | None:  # None: SSL_CERT_FILE unset, see below
        """Return the bundle ``SSL_CERT_FILE`` names, or ``None`` when it is unset.

        ``None`` is the contract, not a failure: with the variable unset, httpx
        uses its own default bundle, and a TLS failure is not ours to rename.
        """
        path = os.environ.get(_CA_BUNDLE_VAR)
        return cls(path) if path else None

    def unloadable(self, cause: OSError) -> CaBundleError:
        """Describe a bundle that could not be loaded when the client was built."""
        # strerror, not str(cause): a FileNotFoundError's str carries the path.
        reason = cause.strerror or type(cause).__name__
        logger.error(
            "%s=%s is not a loadable CA bundle: %s", _CA_BUNDLE_VAR, self._path, reason
        )
        return CaBundleError(
            f"{_CA_BUNDLE_VAR} names a CA bundle that cannot be loaded ({reason}); "
            f"fix the path or unset {_CA_BUNDLE_VAR}"
        )

    def untrusted(self, cause: ssl.SSLCertVerificationError) -> CaBundleError:
        """Describe a bundle that loaded but does not trust the provider."""
        # strerror carries OpenSSL's verify message ("self-signed certificate in
        # certificate chain"); it never carries the bundle path.
        reason = cause.strerror or "certificate verify failed"
        logger.error(
            "%s=%s does not trust the provider's certificate: %s",
            _CA_BUNDLE_VAR,
            self._path,
            reason,
        )
        return CaBundleError(
            f"{_CA_BUNDLE_VAR} names a CA bundle that does not trust the provider's "
            f"certificate ({reason}); fix the bundle or unset {_CA_BUNDLE_VAR}"
        )
