"""The ElevenLabs SDK client every ElevenLabs provider shares, built on first use.

A provider holds an :class:`ElevenLabsClient` and asks it for the SDK only when
it is about to call the API, so composing the daemon never touches the TLS
stack and a bad ``SSL_CERT_FILE`` cannot stop voxd from booting. The deferral
and the naming of a bad bundle live in
:class:`~punt_vox.providers.lazy_sdk.LazySdk`; this class adds what is
ElevenLabs-specific -- the key and the account's health.

This is the one module that imports the ElevenLabs SDK. What the providers need
of it -- the client type their test seam takes, the request and response models
they build and read -- is re-exported here, and every error the SDK raises
leaves as a vox type, so nothing past the providers knows the SDK exists.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Final, Self, final

import httpx
from elevenlabs.client import ElevenLabs
from elevenlabs.core import ApiError, ParsingError
from elevenlabs.types import (
    SpeechToTextChunkResponseModel,
    SpeechToTextWordResponseModel,
    VoiceSettings,
)

from punt_vox.providers.api_key import ApiKeyCheck
from punt_vox.providers.error_body import ErrorBody
from punt_vox.providers.lazy_sdk import LazySdk
from punt_vox.providers.provider_faults import TRANSPORT_ERRORS
from punt_vox.providers.provider_status import ProviderStatusError
from punt_vox.providers.stream_file import StreamFile
from punt_vox.types import HealthCheck
from punt_vox.types_provider_errors import ProviderAuthError

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from pathlib import Path

__all__ = [
    "ElevenLabs",
    "ElevenLabsClient",
    "SpeechToTextChunkResponseModel",
    "SpeechToTextWordResponseModel",
    "VoiceSettings",
]

# The variable the readiness gate names for this provider, not a second literal.
_KEY_CHECK = ApiKeyCheck.for_provider("elevenlabs")

# The statuses an ApiError carries only when a success body failed to decode.
_SUCCESS: Final = range(200, 300)


@final
class ElevenLabsClient:
    """Own one ElevenLabs SDK client: build it on first use, report its health.

    The key is the one in effect when this client's owner was constructed, not
    when the SDK is first built. voxd swaps ``ELEVENLABS_API_KEY`` in the
    environment for the length of a request that carries its own key: a
    provider built inside that window is built for that request and holds that
    caller's key, while the music provider, built at boot, holds the daemon's.
    Reading the key at the first SDK build instead would let a long-lived
    client's build land inside some request's window and keep that caller's
    key for every later use.

    An answered failure is translated here, once, for every call site that goes
    through :meth:`call` -- text-to-speech, the voice list, speech-to-text,
    music, and the subscription check: a 401 into the :class:`ProviderAuthError`
    the wire sends the caller verbatim, any other status into a
    :class:`ProviderStatusError` the caller routes on.
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
        self._api_key = (
            api_key if api_key is not None else os.environ.get(_KEY_CHECK.source)
        )
        # RequestError, not TransportError: httpx raises DecodingError and
        # TooManyRedirects outside TransportError, and the SDK lets both escape.
        self._sdk = LazySdk(self._new_sdk, transport=httpx.RequestError, sdk=sdk)
        return self

    def call[R](self, request: Callable[[ElevenLabs], R]) -> R:
        """Run *request* against the SDK, building the SDK on first use.

        A bundle the build cannot load raises ``CaBundleError``; a key holding a
        character no header may carry raises ``MalformedApiKeyError``. The SDK lets
        httpx's errors escape raw; they leave here translated by
        :class:`~punt_vox.providers.transport_faults.TransportFaults` -- a
        ``CaBundleError`` or ``ProviderConfigError`` (permanent) or a
        ``ProviderTransportError`` (transient). A 401 -- the server rejected the
        key -- becomes ``ProviderAuthError``; any other answered failure becomes
        ``ProviderStatusError``, carrying the status and the server's message.
        So does an answer the SDK cannot parse into its response model.
        """
        try:
            return self._sdk.call(request)
        except ApiError as exc:
            raise self._answered(exc) from exc
        except ParsingError as exc:
            raise self._unparsed(exc) from exc

    def stream_to(
        self, target: Path, open_stream: Callable[[ElevenLabs], Iterable[bytes]]
    ) -> int:
        """Drain the stream *open_stream* starts into *target*, all or nothing.

        The SDK's ``stream()`` is lazy: the request goes out, and every failure
        arrives, only as chunks are pulled -- so the whole drain runs inside the
        translation, not just the call that returns the generator.
        """
        return self.call(lambda sdk: StreamFile(target).write(open_stream(sdk)))

    def key_check(self) -> HealthCheck:
        """Report whether this client has an API key it can send."""
        return _KEY_CHECK.report("ElevenLabs", self._api_key)

    def subscription_check(self) -> HealthCheck:
        """Report the account's subscription tier and character usage."""
        try:
            sub = self.call(lambda sdk: sdk.user.subscription.get())
        except ProviderAuthError as exc:
            reason = f"API key rejected (HTTP {exc.status_code})"
        except ProviderStatusError as exc:
            reason = exc.reason
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
        """Construct the SDK client with the key this client was built with.

        The key is checked here, at the first use, not at construction: voxd
        builds the music client at boot, and a malformed key in ``keys.env``
        must fail the request that sends it -- by name -- not stop the daemon.

        Raises:
            MalformedApiKeyError: The key cannot be sent as a header value.
            ProviderUnavailableError: No key was set when this client was
                built; a request without one would only be refused.
        """
        return ElevenLabs(api_key=_KEY_CHECK.require_set(self._api_key, "elevenlabs"))

    @staticmethod
    def _unparsed(exc: ParsingError) -> ProviderStatusError:
        """Return the vox error for an answer that failed the SDK's validation.

        The body decoded but does not fit the response model; it is not echoed.
        """
        return ProviderStatusError(
            "elevenlabs", exc.status_code, "response did not match the expected schema"
        )

    @staticmethod
    def _answered(exc: ApiError) -> ProviderAuthError | ProviderStatusError:
        """Return the vox error for the server's failed answer *exc*.

        The SDK raises ``ApiError`` on a 2xx too, when the body will not decode
        as JSON; its body is then the raw text, which is never echoed.
        """
        status: int | None = exc.status_code  # None: the SDK reported no status
        if status == 401:
            return ProviderAuthError("elevenlabs", 401)
        detail: str | None  # None: the body gives no reason
        if status in _SUCCESS:
            detail = (
                f"answered HTTP {status} with a response that is not the expected JSON"
            )
        else:
            detail = ErrorBody(exc.body).message
        return ProviderStatusError("elevenlabs", status, detail)
