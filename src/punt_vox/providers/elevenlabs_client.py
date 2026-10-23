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
from typing import TYPE_CHECKING, Self, cast, final

import httpx
from elevenlabs.client import ElevenLabs
from elevenlabs.core import ApiError

from punt_vox.providers.lazy_sdk import LazySdk
from punt_vox.providers.stream_file import StreamFile
from punt_vox.providers.transport_faults import TRANSPORT_ERRORS
from punt_vox.types import HealthCheck

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from pathlib import Path

__all__ = ["ElevenLabsClient"]

_API_KEY_VAR = "ELEVENLABS_API_KEY"


@final
class ElevenLabsClient:
    """Own one ElevenLabs SDK client: build it on first use, report its health.

    The key is fixed when this object is constructed, not when the SDK is
    built. voxd swaps ``ELEVENLABS_API_KEY`` in the environment for the length
    of a request that carries its own key; a long-lived client whose first
    build landed inside that window would keep the caller's key for every
    later use. Reading the key at construction -- after voxd has loaded its
    keys file, and outside any request -- means a client only ever holds the
    key its owner was built with.
    """

    __slots__ = ("_api_key", "_sdk")
    # None: no key was given and none is in the environment; key_check says so.
    _api_key: str | None
    _sdk: LazySdk[ElevenLabs]

    def __new__(
        cls,
        *,
        # Absent: read ELEVENLABS_API_KEY now. Never left to the SDK, whose own
        # default is read when its module is imported, before voxd loads keys.
        api_key: str | None = None,
        # The test seam: absent, the real client is built lazily.
        sdk: ElevenLabs | None = None,
    ) -> Self:
        self = super().__new__(cls)
        self._api_key = api_key if api_key is not None else os.environ.get(_API_KEY_VAR)
        # RequestError, not TransportError: httpx raises DecodingError and
        # TooManyRedirects outside TransportError, and the SDK lets both escape.
        self._sdk = LazySdk(self._new_sdk, transport=httpx.RequestError, sdk=sdk)
        return self

    def call[R](self, request: Callable[[ElevenLabs], R]) -> R:
        """Run *request* against the SDK, building the SDK on first use.

        A bundle the build cannot load raises ``CaBundleError``. The SDK lets
        httpx's errors escape raw; they leave here translated by
        :class:`~punt_vox.providers.transport_faults.TransportFaults` -- a
        ``CaBundleError`` or ``ProviderConfigError`` (permanent) or a
        ``ProviderTransportError`` (transient). ``ApiError`` -- the server
        answered -- passes through for the provider to interpret.
        """
        return self._sdk.call(request)

    def stream_to(
        self, target: Path, open_stream: Callable[[ElevenLabs], Iterable[bytes]]
    ) -> int:
        """Drain the stream *open_stream* starts into *target*, all or nothing.

        The SDK's ``stream()`` is lazy: the request goes out, and every failure
        arrives, only as chunks are pulled -- so the whole drain runs inside the
        translation, not just the call that returns the generator.
        """
        return self._sdk.call(lambda sdk: StreamFile(target).write(open_stream(sdk)))

    def key_check(self) -> HealthCheck:
        """Report whether this client has an API key."""
        if self._api_key:
            return HealthCheck(passed=True, message="ElevenLabs API key: set")
        return HealthCheck(
            passed=False,
            message=f"ElevenLabs API key: not set (export {_API_KEY_VAR})",
        )

    def subscription_check(self) -> HealthCheck:
        """Report the account's subscription tier and character usage."""
        try:
            sub = self._sdk.call(lambda sdk: sdk.user.subscription.get())
        except ApiError as exc:
            reason = self._api_error_message(exc)
        except TRANSPORT_ERRORS as exc:
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

    def _new_sdk(self) -> ElevenLabs:
        """Construct the SDK client with the key this client was built with."""
        return ElevenLabs(api_key=self._api_key)

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
