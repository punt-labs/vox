"""The errors a provider request raises when it fails before the server answers.

:class:`~punt_vox.providers.transport_faults.TransportFaults` decides which one a
given SDK failure is; this module only names them, so a caller that routes on
the type -- the wire reply, the music filler, a call that ends on a permanent
fault -- imports the types without the classifier and its SDK imports.
"""

from __future__ import annotations

from typing import Final

from punt_vox.providers.ca_bundle import CaBundleError

__all__ = [
    "TRANSPORT_ERRORS",
    "ProviderConfigError",
    "ProviderTransportError",
    "UnsendableHeaderError",
]


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


class UnsendableHeaderError(ProviderConfigError):
    """A request header holds a character httpx will not put on the wire.

    Raised ``from None``: the httpx fault beneath it quotes the header value --
    the API key -- and a chained cause is printed wherever the error is logged
    with its traceback.
    """


# Everything a request through TransportFaults can raise: what a health check
# catches to report a failed line instead of crashing.
TRANSPORT_ERRORS: Final = (CaBundleError, ProviderConfigError, ProviderTransportError)
