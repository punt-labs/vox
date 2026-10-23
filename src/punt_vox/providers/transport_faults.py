"""Translate a provider SDK's transport failure into the error voxd routes.

An SDK built on httpx lets transport failures escape raw -- ``httpx.ConnectError``
and its kin are not ``OSError``, so a caller that classifies failures by type
sees an unknown exception and treats it as a bug. Every provider passes such a
failure through :meth:`TransportFaults.translate`, which answers one of two
errors, each chaining the original:

* :class:`~punt_vox.providers.ca_bundle.CaBundleError` -- permanent -- when TLS
  certificate verification failed while ``SSL_CERT_FILE`` is set: the operator's
  bundle is the cause, and retrying cannot help.
* :class:`ProviderTransportError` -- transient -- for everything else: a refused
  connection, a timeout, a dropped stream.
"""

from __future__ import annotations

import ssl
from typing import final

from punt_vox.providers.ca_bundle import CaBundle, CaBundleError

__all__ = ["ProviderTransportError", "TransportFaults"]


class ProviderTransportError(ConnectionError):
    """A provider request failed in transport; a retry may succeed.

    A ``ConnectionError`` so a caller that already treats ``OSError`` as a
    transient network fault needs no new case.
    """


@final
class TransportFaults:
    """Classify a transport failure from any provider SDK."""

    __slots__ = ()

    @classmethod
    def translate(cls, exc: Exception) -> CaBundleError | ProviderTransportError:
        """Return the error to raise, ``from exc``, for the transport failure *exc*."""
        bundle = CaBundle.configured()
        verify = cls._verify_failure(exc)
        if bundle is not None and verify is not None:
            return bundle.untrusted(verify)
        return ProviderTransportError(
            f"provider unreachable ({type(exc).__name__}: {exc}); check the network"
        )

    @staticmethod
    def _verify_failure(exc: BaseException) -> ssl.SSLCertVerificationError | None:
        """Find a certificate-verification failure anywhere in *exc*'s chain.

        httpx wraps the ``ssl`` error in httpcore's, then wraps that in its own,
        so the cause sits two links down; an SDK may add a link of its own.
        ``None`` means the failure was not a verification failure at all.
        """
        seen: set[int] = set()
        current: BaseException | None = exc  # None ends the walk: chain exhausted
        while current is not None and id(current) not in seen:
            if isinstance(current, ssl.SSLCertVerificationError):
                return current
            seen.add(id(current))
            current = current.__cause__ or current.__context__
        return None
