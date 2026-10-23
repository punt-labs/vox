"""An SDK client built on first use, so composing voxd never touches TLS.

Constructing an httpx-based SDK client is not inert: httpx loads
``SSL_CERT_FILE`` into a TLS context right then. A provider that built its
client while the daemon composed its subsystems would let a stale CA path stop
voxd from booting at all. :class:`LazySdk` defers the build to the first call
that needs the API, names a bad bundle when the build fails, and translates the
SDK's transport failures for every request made through :meth:`LazySdk.call`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Self, final

from punt_vox.providers.ca_bundle import CaBundle
from punt_vox.providers.transport_faults import TransportFaults

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["LazySdk"]


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
        """
        if self._sdk is None:
            self._sdk = self._build()
        return self._sdk

    def call[R](self, request: Callable[[T], R]) -> R:
        """Run *request* against the SDK, translating a transport failure.

        A transport failure leaves as whatever
        :meth:`~punt_vox.providers.transport_faults.TransportFaults.translate`
        answers -- a ``CaBundleError``, ``ProviderConfigError``, or
        ``ProviderTransportError`` -- chaining the original. Anything else --
        the server answered, and the provider interprets the answer -- passes
        through unchanged.
        """
        try:
            return request(self.get())
        except self._transport as exc:
            raise TransportFaults.translate(exc) from exc

    def _build(self) -> T:
        try:
            return self._factory()
        except OSError as exc:
            bundle = CaBundle.configured()
            if bundle is None:
                raise
            raise bundle.unloadable(exc) from exc
