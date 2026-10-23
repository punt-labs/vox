"""An SDK client built on first use, so composing voxd never touches TLS.

Constructing an httpx-based SDK client is not inert: httpx loads
``SSL_CERT_FILE`` into a TLS context right then. A provider that built its
client while the daemon composed its subsystems would let a stale CA path stop
voxd from booting at all. :class:`LazySdk` defers the build to the first call
that needs the API, names a bad bundle when the build fails, and translates the
SDK's transport failures for every request made through :meth:`LazySdk.call`.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Self, final

from punt_vox.providers.ca_bundle import CaBundle, CaBundleError
from punt_vox.providers.provider_faults import (
    ProviderConfigError,
    UnsendableHeaderError,
)
from punt_vox.providers.transport_faults import TransportFaults

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["LazySdk"]

logger = logging.getLogger(__name__)


@final
class LazySdk[T]:
    """Own one SDK client: build it with *factory* on first use, then keep it.

    *transport* is the exception type the SDK raises when a request fails in
    transport -- ``httpx.TransportError`` for an SDK that lets httpx's errors
    escape raw, the SDK's own connection error for one that wraps them.
    """

    __slots__ = ("_factory", "_sdk", "_transport")
    _factory: Callable[[], T]
    _transport: type[Exception]
    # None until first use -- deferring the build is the point of this class.
    _sdk: T | None

    def __new__(
        cls,
        factory: Callable[[], T],
        *,
        transport: type[Exception],
        sdk: T | None = None,
    ) -> Self:
        # ``sdk`` is the test seam: absent, *factory* builds the client lazily.
        self = super().__new__(cls)
        self._factory = factory
        self._transport = transport
        self._sdk = sdk
        return self

    def get(self) -> T:
        """Return the SDK client, building it on first use.

        A failed build is not remembered, so the next call tries again --
        fixing the bundle needs no daemon restart.

        Raises:
            CaBundleError: ``SSL_CERT_FILE`` is set and cannot be loaded.
            ProviderConfigError: The constructor refused the host's proxy or
                base URL, or the factory refused the key.
        """
        if self._sdk is None:
            self._sdk = self._build()
        return self._sdk

    def call[R](self, request: Callable[[T], R]) -> R:
        """Run *request* against the SDK, translating a transport failure.

        A transport failure leaves as whatever
        :meth:`~punt_vox.providers.transport_faults.TransportFaults.translate`
        answers -- a ``CaBundleError``, ``ProviderConfigError``, or
        ``ProviderTransportError`` -- chaining the original, except under an
        ``UnsendableHeaderError``, whose original quotes the API key. Anything
        else -- the server answered, and the provider interprets the answer --
        passes through unchanged.
        """
        try:
            return request(self.get())
        except self._transport as exc:
            fault = TransportFaults.translate(exc)
            cause = None if isinstance(fault, UnsendableHeaderError) else exc
            raise fault from cause

    def _build(self) -> T:
        try:
            return self._factory()
        except ProviderConfigError:
            raise  # the factory already named the fault -- a malformed key
        except (OSError, ValueError, ImportError) as exc:
            renamed = self._renamed(exc)
            if renamed is None:
                raise
            raise renamed from exc

    @classmethod
    def _renamed(
        cls, exc: OSError | ValueError | ImportError
    ) -> CaBundleError | ProviderConfigError | None:  # None: not ours to rename
        """Name the host setting a failed construction points at, if any.

        An ``OSError`` is ``SSL_CERT_FILE`` only when it is set; a ``ValueError``
        is httpx refusing the proxy or base URL; an ``ImportError`` is ours only
        in httpx's words for a SOCKS proxy without socksio -- any other is a
        broken install.
        """
        if isinstance(exc, OSError):
            bundle = CaBundle.configured()
            return bundle.unloadable(exc) if bundle is not None else None
        if isinstance(exc, ImportError):
            if "socksio" not in str(exc):
                return None
            logger.error("provider client cannot be built: %r", exc)
            return ProviderConfigError(
                "a SOCKS proxy is set (HTTPS_PROXY or ALL_PROXY) but SOCKS support "
                "is not installed; use an HTTP proxy or unset it"
            )
        return cls._unbuildable(exc)

    @staticmethod
    def _unbuildable(fault: ValueError) -> ProviderConfigError:
        """Describe a client the constructor refused, without the fault's text.

        httpx rejects an unusable proxy URL here, quoting it; its repr masks a
        password, so the full fault goes to the log, but the URL still names the
        proxy host and user, and this message crosses the wire.
        """
        logger.error("provider client cannot be built: %r", fault)
        return ProviderConfigError(
            f"provider client cannot be built ({type(fault).__name__}); check the "
            "proxy settings (HTTPS_PROXY, ALL_PROXY) and the provider base URL"
        )
