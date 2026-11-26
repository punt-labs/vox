"""The authenticated HTTP connection to the ElevenLabs Conversational AI API."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, Self, final

import httpx

from punt_vox.voxd.voice_agent.fault import ConvAIError, UnknownCreateError
from punt_vox.voxd.voice_agent.reply import Reply

if TYPE_CHECKING:
    from punt_vox.voxd.voice_agent.contracts import CreateConfig

__all__ = ["REQUEST_TIMEOUT_S", "SIGNED_URL", "UNKNOWN_CREATE", "Wire"]

SIGNED_URL: Final = "/conversation/get-signed-url"
UNKNOWN_CREATE: Final = "; check the ElevenLabs workspace for an unrecorded resource"
REQUEST_TIMEOUT_S: Final = 10.0  # Per request; a delete is a read, then a delete.
_CONVAI: Final = "/v1/convai"


@final
class Wire:
    """Send requests under ``/v1/convai``; failures become :class:`ConvAIError`."""

    __slots__ = ("_http",)
    _http: httpx.Client

    def __new__(cls, base_url: str, api_key: str) -> Self:
        """Connect to *base_url*, sending *api_key* with every request."""
        self = super().__new__(cls)
        self._http = httpx.Client(
            base_url=base_url,
            headers={"xi-api-key": api_key},
            timeout=REQUEST_TIMEOUT_S,
        )
        return self

    def close(self) -> None:
        """Release the connection pool."""
        self._http.close()

    def request(
        self,
        method: str,
        path: str,
        body: CreateConfig | None = None,  # Only create requests have a JSON body.
        *,
        params: dict[str, str] | None = None,  # Only GET and DELETE take query params.
        tolerate_404: bool = False,  # True where an absent resource is an answer.
    ) -> Reply:
        """Send one request; return its reply, or raise on any other failure."""
        try:
            response = self._http.request(
                method, _CONVAI + path, json=body, params=params
            )
        except httpx.HTTPError as exc:
            msg = f"ElevenLabs {method} {path} failed ({type(exc).__name__})"
            if method == "POST":
                raise UnknownCreateError(msg + UNKNOWN_CREATE) from None
            raise ConvAIError(msg) from None
        if response.is_success or (tolerate_404 and response.status_code == 404):
            return Reply(response)
        msg = self._status_message(method, path, response.status_code)
        raise ConvAIError(msg)

    @staticmethod
    def _status_message(method: str, path: str, code: int) -> str:
        if code == 404 and path == SIGNED_URL:
            return (
                "The ElevenLabs agent no longer exists; tear down the Vox voice "
                "agent to reprovision (HTTP 404)"
            )
        message = f"ElevenLabs {method} {path} returned HTTP {code}"
        if code in (401, 403):
            return f"{message}; check the key's ElevenLabs Agents access"
        return message
