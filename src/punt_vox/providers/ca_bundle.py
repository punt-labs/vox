"""The CA certificates a provider's TLS trusts, and the two ways they break it.

httpx picks its trust anchors when an SDK client is constructed, not per
request: ``SSL_CERT_FILE`` if set, else ``SSL_CERT_DIR`` if set, else certifi's
bundle. A bad choice surfaces in one of two places:

* **Build time.** ``SSL_CERT_FILE`` is missing or holds no certificate, and the
  SDK constructor raises a bare ``OSError`` that names neither the variable nor
  the path. (``SSL_CERT_DIR`` cannot fail here: OpenSSL reads it lazily.)
* **Request time.** The anchors load but do not trust the provider's
  certificate, and the request fails with ``ssl.SSLCertVerificationError``
  buried in the SDK's exception chain.

Either way the operator needs to be told what TLS trusted. :class:`CaBundle`
turns both into a :class:`CaBundleError` whose message names the variable (or
the default bundle) and the reason -- but never the path, because the message
crosses the wire to every client, and the path is a host detail. The path is
logged once, at ERROR, where the failure is translated, so the operator finds
it in ``vox.log``.

Provider-neutral: every SDK that rides on httpx fails this way, so every
provider routes its failures through this one module rather than its own copy.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Final, Self, final

if TYPE_CHECKING:
    import ssl

logger = logging.getLogger(__name__)

__all__ = ["CaBundle", "CaBundleError"]

_CA_FILE_VAR: Final = "SSL_CERT_FILE"
_CA_DIR_VAR: Final = "SSL_CERT_DIR"

# What each variable names, in the order httpx consults them.
_NOUNS: Final = {_CA_FILE_VAR: "CA bundle", _CA_DIR_VAR: "CA directory"}


class CaBundleError(ValueError):
    """The CA certificates TLS used cannot verify the provider.

    A ``ValueError`` because it is a configuration fault, not a transient I/O
    one: retrying with the same environment fails the same way, and the wire
    reply routes a ``ValueError`` message to the caller verbatim.
    """


@final
class CaBundle:
    """The trust anchors one variable names, describing its own failures."""

    __slots__ = ("_path", "_var")
    _path: str
    # None: neither variable is set, so httpx trusts certifi's bundle -- no
    # variable to name, and no path of ours to log.
    _var: str | None

    def __new__(cls, path: str, *, var: str | None = _CA_FILE_VAR) -> Self:
        self = super().__new__(cls)
        self._path = path
        self._var = var
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
    def in_effect(cls) -> Self:
        """Return the trust anchors httpx used: the file, else the dir, else certifi."""
        for var in _NOUNS:
            if path := os.environ.get(var):
                return cls(path, var=var)
        return cls("", var=None)

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
        """Describe trust anchors that loaded but do not trust the provider."""
        reason = self._verify_reason(cause)
        if self._var is None:
            logger.error(
                "the default CA bundle does not trust the provider's certificate: %s",
                reason,
            )
            return CaBundleError(
                "the default CA bundle (certifi) does not trust the provider's "
                f"certificate ({reason}); behind a TLS-inspecting proxy, set "
                f"{_CA_FILE_VAR} to a bundle that holds its CA"
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
