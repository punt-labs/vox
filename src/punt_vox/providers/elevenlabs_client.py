"""The ElevenLabs SDK client every ElevenLabs provider shares, built on first use.

Constructing the SDK client is not inert: it builds an httpx transport, and
httpx loads ``SSL_CERT_FILE`` into a TLS context right then, not per request. A
stale or wrong CA path therefore fails *construction* with a bare
``FileNotFoundError`` that names neither the variable nor the path. Two rules
follow, and this module is where both live:

* **Build late.** A provider holds an :class:`ElevenLabsClient` and asks it for
  the SDK only when it is about to call the API, so composing the daemon never
  touches the TLS stack and a bad bundle cannot stop voxd from booting.
* **Name the cause.** When the build fails while ``SSL_CERT_FILE`` is set, the
  failure is re-raised as :class:`CaBundleError`, which carries the variable and
  the path -- the two things the operator who set it needs to see.
"""

from __future__ import annotations

import os
from typing import Self, cast, final

from elevenlabs.client import ElevenLabs
from elevenlabs.core import ApiError

from punt_vox.types import HealthCheck

__all__ = ["CaBundleError", "ElevenLabsClient"]

_API_KEY_VAR = "ELEVENLABS_API_KEY"
_CA_BUNDLE_VAR = "SSL_CERT_FILE"


class CaBundleError(ValueError):
    """``SSL_CERT_FILE`` names a CA bundle the TLS stack cannot load.

    A ``ValueError`` because it is a bad configuration value, not a transient
    I/O fault: retrying with the same environment fails the same way, and the
    wire reply routes a ``ValueError`` message to the caller verbatim.
    """

    @classmethod
    def unloadable(cls, cafile: str, cause: OSError) -> Self:
        """Describe why *cafile* could not be loaded, naming the variable."""
        reason = cause.strerror or str(cause)
        return cls(
            f"{_CA_BUNDLE_VAR}={cafile} is not a loadable CA bundle ({reason}); "
            f"fix the path or unset {_CA_BUNDLE_VAR}"
        )


@final
class ElevenLabsClient:
    """Own one ElevenLabs SDK client: build it on first use, report its health."""

    __slots__ = ("_sdk",)
    # None until first use -- deferring the build is the point of this class.
    _sdk: ElevenLabs | None

    def __new__(cls, *, sdk: ElevenLabs | None = None) -> Self:
        # ``sdk`` is the test seam: absent, the real client is built lazily.
        self = super().__new__(cls)
        self._sdk = sdk
        return self

    def sdk(self) -> ElevenLabs:
        """Return the SDK client, building it on first use.

        A failed build is not remembered, so the next call tries again.

        Raises:
            CaBundleError: ``SSL_CERT_FILE`` is set and cannot be loaded.
        """
        if self._sdk is None:
            self._sdk = self._build()
        return self._sdk

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
    def _build() -> ElevenLabs:
        """Construct the SDK client, translating a bad CA bundle into its name.

        The key is read here rather than left to the SDK: the SDK's ``api_key``
        default is evaluated when its module is imported, before voxd has loaded
        its keys file into the environment.
        """
        try:
            return ElevenLabs(api_key=os.environ.get(_API_KEY_VAR))
        except OSError as exc:
            cafile = os.environ.get(_CA_BUNDLE_VAR)
            if not cafile:
                raise
            raise CaBundleError.unloadable(cafile, exc) from exc

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
