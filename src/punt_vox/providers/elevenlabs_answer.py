"""What a failed ElevenLabs answer means, in vox's terms.

The SDK raises on every answer it cannot hand back as a response model; this
class reads the status and decoded body it carries and names the vox error the
caller routes on. It takes the parts, not the SDK's error, so the SDK stays
inside :mod:`~punt_vox.providers.elevenlabs_client`.
"""

from __future__ import annotations

import logging
from typing import Final, Self, final

from punt_vox.providers.error_body import ErrorBody
from punt_vox.providers.provider_refusal import ProviderRefusalError
from punt_vox.providers.provider_status import ProviderStatusError

__all__ = ["ElevenLabsAnswer"]

logger = logging.getLogger(__name__)

_PROVIDER: Final = "elevenlabs"
# The statuses an ApiError carries only when a success body failed to decode.
_SUCCESS: Final = range(200, 300)
# The statuses that refuse the credentials -- a bad key, or an account barred.
_REFUSED: Final = (401, 403)


@final
class ElevenLabsAnswer:
    """One answer ElevenLabs gave that the SDK could not return as a response.

    A 401 or 403 is a :class:`ProviderRefusalError`, which the wire sends the
    caller verbatim, carrying the server's code for the refusal; any other
    status is a :class:`ProviderStatusError` the caller routes on. The SDK
    raises on a 2xx too, when the body will not decode as JSON; its body is
    then the raw text, which is never echoed.
    """

    __slots__ = ("_body", "_status")
    _status: int | None  # None: the SDK reported no status
    _body: ErrorBody

    def __new__(cls, status: int | None, body: object) -> Self:
        self = super().__new__(cls)
        self._status = status
        self._body = ErrorBody(body)
        return self

    def error(self) -> ProviderRefusalError | ProviderStatusError:
        """Return the vox error for this answer."""
        if self._status in _REFUSED:
            return self._refusal(self._status)
        return ProviderStatusError(_PROVIDER, self._status, self._detail)

    def off_schema(self) -> ProviderStatusError:
        """Return the vox error for a body that decoded but fails the response
        model; the body is not echoed."""
        return ProviderStatusError(
            _PROVIDER, self._status, "response did not match the expected schema"
        )

    @property
    def _detail(self) -> str | None:  # None: the body gives no reason
        """Return the reason this failure is reported with."""
        if self._status in _SUCCESS:
            return (
                f"answered HTTP {self._status} "
                "with a response that is not the expected JSON"
            )
        return self._body.message

    def _refusal(self, status: int) -> ProviderRefusalError:
        """Return the refusal the caller sees, logging the server's code for it.

        ElevenLabs names why it refused in ``detail.status`` -- an invalid key,
        unusual activity on the account, an exhausted quota -- and each wants a
        different fix, so the code reaches both the log and the caller. Only an
        identifier is taken, never the free text; a refusal without one -- a
        proxy's HTML page, a raw-text body -- still logs its status, so the
        refusal leaves a trace in vox.log.
        """
        refusal = ProviderRefusalError.from_code(_PROVIDER, status, self._body.status)
        if refusal.reason is None:
            logger.error("ElevenLabs refused (HTTP %d) with no error code", status)
        else:
            logger.error("ElevenLabs refused (%s)", refusal.answer)
        return refusal
