"""Translate a provider SDK's transport failure into the error voxd routes.

An SDK built on httpx lets transport failures escape raw -- ``httpx.ConnectError``
and its kin are not ``OSError``, so a caller that classifies failures by type
sees an unknown exception and treats it as a bug. Every provider passes such a
failure through :meth:`TransportFaults.translate`, which answers one of three
errors (:mod:`punt_vox.providers.provider_faults`), each chaining the original:

* :class:`~punt_vox.providers.ca_bundle.CaBundleError` -- permanent -- when TLS
  certificate verification failed: the CA certificates in effect
  (``SSL_CERT_FILE``, ``SSL_CERT_DIR``, or the client's default anchors) do not
  trust the provider, or its certificate names another host or has expired,
  and retrying cannot help.
* :class:`ProviderConfigError` -- permanent -- when the request cannot be sent
  as the host configured it: a base URL with no usable scheme, a proxy that
  refuses the credentials it was given, a header httpx will not put on the wire
  (:class:`UnsendableHeaderError`, whose cause is never chained -- see there).
* :class:`ProviderTransportError` -- transient -- for everything else: a refused
  connection, a timeout, a dropped stream, a proxy that answers 5xx or cannot
  reach the provider itself.
"""

from __future__ import annotations

import logging
import ssl
from typing import Final, Self, final

import httpx
import httpx2

from punt_vox.providers.ca_bundle import CaBundle, CaBundleError, TrustDefault
from punt_vox.providers.provider_faults import (
    ProviderConfigError,
    ProviderTransportError,
    UnsendableHeaderError,
)

logger = logging.getLogger(__name__)

__all__ = ["TransportFaults"]

# Each tuple holds both packages' class: the ElevenLabs SDK rides on httpx, the
# OpenAI SDK on httpx2, its fork, and the two packages' error classes are
# unrelated types.

# A URL with no usable scheme: a fault in what the host told httpx to send.
_CONFIG_FAULTS: Final = (httpx.UnsupportedProtocol, httpx2.UnsupportedProtocol)

# A header value httpx refuses to send. The fault's text quotes the value, and
# the header carrying a malformed value is almost always the API key.
_HEADER_FAULTS: Final = (httpx.LocalProtocolError, httpx2.LocalProtocolError)

# Anything the proxy answered instead of a tunnel -- configuration or not,
# depending on what it said (_PROXY_REFUSALS).
_PROXY_FAULTS: Final = (httpx.ProxyError, httpx2.ProxyError)

# The proxy replies only a change of proxy credentials can fix. httpcore words
# an HTTP proxy's refused CONNECT as "<status> <reason>" and a SOCKS proxy's as
# fixed sentences; every other proxy reply -- a 5xx, a SOCKS "could not
# connect" -- is the proxy or the path beyond it failing, and a retry may pass.
_PROXY_REFUSALS: Final = (
    "407 ",
    "403 ",
    "Invalid username/password",
    "Requested ",  # SOCKS: the proxy wants a different authentication method
)


@final
class TransportFaults:
    """Classify a transport failure from any provider SDK."""

    __slots__ = ()

    @classmethod
    def translate(
        cls, exc: Exception
    ) -> CaBundleError | ProviderConfigError | ProviderTransportError:
        """Return the error to raise for the transport failure *exc*.

        Raise it ``from exc`` -- unless it is an :class:`UnsendableHeaderError`,
        which is raised ``from None``.
        """
        chain = _FaultChain(exc)
        cert = chain.first(ssl.SSLCertVerificationError)
        if isinstance(cert, ssl.SSLCertVerificationError):
            return CaBundle.in_effect(default=chain.trust_default).untrusted(cert)
        if (fault := chain.first(_HEADER_FAULTS)) is not None:
            return cls._unsendable_header(fault)
        if (fault := chain.first(_PROXY_FAULTS)) is not None:
            return cls._proxy_fault(fault, chain.innermost)
        if (fault := chain.first(_CONFIG_FAULTS)) is not None:
            return cls._misconfigured(fault)
        inner = chain.innermost
        return ProviderTransportError(
            f"provider unreachable ({type(inner).__name__}: {inner}); check the network"
        )

    @classmethod
    def _proxy_fault(
        cls, fault: BaseException, inner: BaseException
    ) -> ProviderConfigError | ProviderTransportError:
        """Classify what the proxy answered instead of a tunnel.

        httpcore's proxy texts are a status line or a fixed SOCKS sentence --
        never the proxy URL -- so the transient message may quote them.
        """
        if str(fault).startswith(_PROXY_REFUSALS):
            return cls._misconfigured(fault)
        return ProviderTransportError(
            f"provider unreachable through the proxy ({type(inner).__name__}: "
            f"{inner}); check the proxy (HTTPS_PROXY, ALL_PROXY), or retry"
        )

    @staticmethod
    def _unsendable_header(fault: BaseException) -> UnsendableHeaderError:
        """Describe a header httpx refused, without the fault's text.

        The text quotes the header value -- the API key -- so only the type is
        logged, and the message names the likely culprit instead.
        """
        logger.error(
            "provider request cannot be sent: %s (header value withheld)",
            type(fault).__name__,
        )
        return UnsendableHeaderError(
            "a request header has an illegal character; check the API key for "
            "stray whitespace or newlines"
        )

    @staticmethod
    def _misconfigured(fault: BaseException) -> ProviderConfigError:
        """Describe a request that cannot be sent, without the fault's own text.

        The text can name the proxy host, and this message crosses the wire; the
        full fault goes to the log instead.
        """
        logger.error("provider request cannot be sent: %r", fault)
        return ProviderConfigError(
            f"provider request cannot be sent ({type(fault).__name__}); check the "
            "proxy settings (HTTPS_PROXY, ALL_PROXY) and the provider base URL"
        )


@final
class _FaultChain:
    """One failure and every exception beneath it, outermost first.

    httpx wraps the ``ssl`` error in httpcore's, then wraps that in its own, and
    an SDK may add a link of its own -- by ``raise from`` (``__cause__``) or by
    raising inside ``except`` (``__context__``). A cycle ends the walk.
    """

    __slots__ = ("_links",)
    _links: tuple[BaseException, ...]

    def __new__(cls, exc: BaseException) -> Self:
        self = super().__new__(cls)
        links: list[BaseException] = []
        seen: set[int] = set()
        current: BaseException | None = exc  # None ends the walk: chain exhausted
        while current is not None and id(current) not in seen:
            links.append(current)
            seen.add(id(current))
            current = current.__cause__ or current.__context__
        self._links = tuple(links)
        return self

    @property
    def innermost(self) -> BaseException:
        """Return the deepest link: it says what failed ("Connection refused").

        The SDK's wrapper on top ("Connection error.") says only that something
        did.
        """
        return self._links[-1]

    @property
    def trust_default(self) -> TrustDefault:
        """Return the anchors the failing client falls back to with neither
        ``SSL_CERT_FILE`` nor ``SSL_CERT_DIR`` set.

        An httpx2 link means the OpenAI SDK's client, which trusts the system
        store; otherwise it was httpx, which trusts certifi.
        """
        return "system" if self.first(httpx2.HTTPError) else "certifi"

    def first(
        self, kinds: type[BaseException] | tuple[type[BaseException], ...]
    ) -> BaseException | None:  # None: no link is one of *kinds*
        """Return the outermost link that is one of *kinds*."""
        return next((link for link in self._links if isinstance(link, kinds)), None)
