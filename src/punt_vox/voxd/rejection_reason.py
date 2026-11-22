"""Fixed audit codes for caller rejections, independent of rejection details."""

from __future__ import annotations

from enum import StrEnum

from punt_vox.providers.ca_bundle import CaBundleError
from punt_vox.providers.provider_faults import ProviderConfigError
from punt_vox.types_errors import VoiceNotFoundError
from punt_vox.types_provider_errors import (
    ProviderAuthError,
    ProviderUnavailableError,
    UnknownProviderError,
)


class RejectionReason(StrEnum):
    """The closed vocabulary permitted in durable rejection records."""

    INVALID_REQUEST = "invalid_request"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_AUTH = "provider_auth"
    UNKNOWN_PROVIDER = "unknown_provider"
    VOICE_NOT_FOUND = "voice_not_found"
    CA_BUNDLE = "ca_bundle"
    PROVIDER_CONFIG = "provider_config"

    @classmethod
    def for_exception(cls, cause: str | ValueError) -> RejectionReason:
        """Classify by exception type without inspecting its message."""
        for exception_type, reason in _REASONS:
            if isinstance(cause, exception_type):
                return reason
        return cls.INVALID_REQUEST


_REASONS = (
    (ProviderUnavailableError, RejectionReason.PROVIDER_UNAVAILABLE),
    (ProviderAuthError, RejectionReason.PROVIDER_AUTH),
    (UnknownProviderError, RejectionReason.UNKNOWN_PROVIDER),
    (VoiceNotFoundError, RejectionReason.VOICE_NOT_FOUND),
    (CaBundleError, RejectionReason.CA_BUNDLE),
    (ProviderConfigError, RejectionReason.PROVIDER_CONFIG),
)
