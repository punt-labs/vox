"""The provider failures whose message the caller can act on.

Each names something the caller's state or host asked for that the daemon
cannot honour: a provider with no credentials here, credentials the provider
rejected, a provider name the registry does not know, a voice the provider
does not offer, or an ``SSL_CERT_FILE`` the provider's TLS cannot use. A
request handler sends these verbatim as an ``error`` frame, so the sentence
naming the fix reaches the caller; anything else is a daemon-side fault and is
laundered to ``"operation failed"``.

The set is closed on purpose. Every member is a ``ValueError``, but widening the
catch to every ``ValueError`` would report genuine daemon-side bugs -- an
internal invariant that happens to raise one -- as caller rejections.
"""

from __future__ import annotations

from typing import Final

from punt_vox.providers.ca_bundle import CaBundleError
from punt_vox.types_errors import VoiceNotFoundError
from punt_vox.types_provider_errors import (
    ProviderAuthError,
    ProviderUnavailableError,
    UnknownProviderError,
)

__all__ = ["CALLER_ERRORS"]

CALLER_ERRORS: Final[tuple[type[ValueError], ...]] = (
    ProviderUnavailableError,
    ProviderAuthError,
    UnknownProviderError,
    VoiceNotFoundError,
    CaBundleError,
)
