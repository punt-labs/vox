"""The ElevenLabs SDK client every ElevenLabs provider shares, built on first use.

A provider holds an :class:`ElevenLabsClient` and asks it for the SDK only when
it is about to call the API, so composing the daemon never touches the TLS
stack and a bad ``SSL_CERT_FILE`` cannot stop voxd from booting. The deferral
and the naming of a bad bundle live in
:class:`~punt_vox.providers.lazy_sdk.LazySdk`; this class adds what is
ElevenLabs-specific -- the key and the account's health.
"""

from __future__ import annotations

import os
from typing import Self, cast, final

import httpx
from elevenlabs.client import ElevenLabs
from elevenlabs.core import ApiError

from punt_vox.providers.ca_bundle import CaBundleError
from punt_vox.providers.lazy_sdk import LazySdk
from punt_vox.providers.transport_faults import TransportFaults
from punt_vox.types import HealthCheck

__all__ = ["ElevenLabsClient"]

_API_KEY_VAR = "ELEVENLABS_API_KEY"


@final
class ElevenLabsClient:
    """Own one ElevenLabs SDK client: build it on first use, report its health."""

    __slots__ = ("_sdk",)
    _sdk: LazySdk[ElevenLabs]

    def __new__(cls, *, sdk: ElevenLabs | None = None) -> Self:
        # ``sdk`` is the test seam: absent, the real client is built lazily.
        self = super().__new__(cls)
        self._sdk = LazySdk(self._new_sdk, sdk=sdk)
        return self

    def sdk(self) -> ElevenLabs:
        """Return the SDK client, building it on first use.

        Raises:
            CaBundleError: ``SSL_CERT_FILE`` is set and cannot be loaded.
        """
        return self._sdk.get()

    @staticmethod
    def key_check() -> HealthCheck:
        """Report whether the API key is present in the environment."""
        if os.environ.get(_API_KEY_VAR):
            return HealthCheck(passed=True, message="ElevenLabs API key: set")
        return HealthCheck(
            passed=False,
            message=f"ElevenLabs API key: not set (export {_API_KEY_VAR})",
        )

    def subscription_check(self) -> HealthCheck:
        """Report the account's subscription tier and character usage."""
        try:
            sub = self.sdk().user.subscription.get()
        except ApiError as exc:
            reason = self._api_error_message(exc)
        except httpx.TransportError as exc:
            reason = str(TransportFaults.translate(exc))
        except (CaBundleError, OSError) as exc:
            reason = str(exc)
        else:
            return HealthCheck(
                passed=True,
                message=(
                    f"ElevenLabs subscription: {sub.tier} "
                    f"({sub.character_count:,}/{sub.character_limit:,} chars)"
                ),
            )
        return HealthCheck(passed=False, message=f"ElevenLabs subscription: {reason}")

    @staticmethod
    def _new_sdk() -> ElevenLabs:
        """Construct the SDK client.

        The key is read here rather than left to the SDK: the SDK's ``api_key``
        default is evaluated when its module is imported, before voxd has loaded
        its keys file into the environment.
        """
        return ElevenLabs(api_key=os.environ.get(_API_KEY_VAR))

    @staticmethod
    def _api_error_message(exc: ApiError) -> str:
        """Extract the human-readable message from an ``ApiError`` body."""
        body: object = exc.body
        if isinstance(body, dict):
            detail = cast("dict[str, object]", body).get("detail")
            if isinstance(detail, dict):
                message = cast("dict[str, object]", detail).get("message")
                if isinstance(message, str) and message:
                    return message
        return f"HTTP {exc.status_code}"
