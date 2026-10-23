"""Translate a provider SDK's transport failure into the error voxd routes.

An SDK built on httpx lets transport failures escape raw -- ``httpx.ConnectError``
and its kin are not ``OSError``, so a caller that classifies failures by type
sees an unknown exception and treats it as a bug. Every provider passes such a
failure through :meth:`TransportFaults.translate`, which answers one of three
errors, each chaining the original:

* :class:`~punt_vox.providers.ca_bundle.CaBundleError` -- permanent -- when TLS
  certificate verification failed: the CA certificates in effect (whether
  ``SSL_CERT_FILE``, ``SSL_CERT_DIR``, or the default bundle) do not trust the
  provider, and retrying cannot help.
* :class:`ProviderConfigError` -- permanent -- when httpx refused to send the
  request at all: a proxy it cannot use, a base URL with no usable scheme, a
  header it will not put on the wire.
* :class:`ProviderTransportError` -- transient -- for everything else: a refused
  connection, a timeout, a dropped stream.
"""

from __future__ import annotations

import logging
import ssl
from typing import Final, final

import httpx
import httpx2

from punt_vox.providers.ca_bundle import CaBundle, CaBundleError

logger = logging.getLogger(__name__)

__all__ = [
    "TRANSPORT_ERRORS",
    "ProviderConfigError",
    "ProviderTransportError",
    "TransportFaults",
]

# Faults in what the host told httpx to send, not in the network it sent it over.
# Both packages': the ElevenLabs SDK rides on httpx, the OpenAI SDK on httpx2,
# its fork, and the two packages' error classes are unrelated types.
_CONFIG_FAULTS: Final = (
    httpx.UnsupportedProtocol,
    httpx.ProxyError,
    httpx.LocalProtocolError,
    httpx2.UnsupportedProtocol,
    httpx2.ProxyError,
    httpx2.LocalProtocolError,
)


class ProviderTransportError(ConnectionError):
    """A provider request failed in transport; a retry may succeed.

    A ``ConnectionError`` so a caller that already treats ``OSError`` as a
    transient network fault needs no new case.
    """


class ProviderConfigError(ValueError):
    """A provider request cannot be sent as the host configured it.

    A ``ValueError`` for the reason :class:`CaBundleError` is one: retrying with
    the same environment fails the same way, and the wire reply routes the
    message -- which names the fault and where to look -- to the caller verbatim.
    """


# Everything a request through TransportFaults can raise: what a health check
# catches to report a failed line instead of crashing.
TRANSPORT_ERRORS: Final = (CaBundleError, ProviderConfigError, ProviderTransportError)


@final
class TransportFaults:
    """Classify a transport failure from any provider SDK."""

    __slots__ = ()

    @classmethod
    def translate(
        cls, exc: Exception
    ) -> CaBundleError | ProviderConfigError | ProviderTransportError:
        """Return the error to raise, ``from exc``, for the transport failure *exc*."""
        chain = cls._chain(exc)
        for link in chain:
            if isinstance(link, ssl.SSLCertVerificationError):
                return CaBundle.in_effect().untrusted(link)
        for link in chain:
            if isinstance(link, _CONFIG_FAULTS):
                return cls._misconfigured(link)
        # The innermost link says what failed ("Connection refused"); the SDK's
        # wrapper on top ("Connection error.") says only that something did.
        inner = chain[-1]
        return ProviderTransportError(
            f"provider unreachable ({type(inner).__name__}: {inner}); check the network"
        )

    @staticmethod
    def _misconfigured(
        fault: httpx.RequestError | httpx2.RequestError,
    ) -> ProviderConfigError:
        """Describe a request httpx refused to send, without the fault's own text.

        The text can quote the proxy URL, credentials included, and this message
        crosses the wire; the full fault goes to the log instead.
        """
        logger.error("provider request cannot be sent: %r", fault)
        return ProviderConfigError(
            f"provider request cannot be sent ({type(fault).__name__}); check the "
            "proxy settings (HTTPS_PROXY, ALL_PROXY) and the provider base URL"
        )

    @staticmethod
    def _chain(exc: BaseException) -> tuple[BaseException, ...]:
        """Return *exc* and every exception beneath it, outermost first.

        httpx wraps the ``ssl`` error in httpcore's, then wraps that in its own,
        and an SDK may add a link of its own -- by ``raise from`` (``__cause__``)
        or by raising inside ``except`` (``__context__``). A cycle ends the walk.
        """
        links: list[BaseException] = []
        seen: set[int] = set()
        current: BaseException | None = exc  # None ends the walk: chain exhausted
        while current is not None and id(current) not in seen:
            links.append(current)
            seen.add(id(current))
            current = current.__cause__ or current.__context__
        return tuple(links)
