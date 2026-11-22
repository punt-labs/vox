"""Register client tools and fetch signed conversations through the REST API."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Self, cast, final

import httpx

from punt_vox.voxd.voice_agent.fault import ConvAIError

__all__ = ["ClientTool", "ConvAIControl"]

_API = "https://api.elevenlabs.io"
_CONVAI = "/v1/convai"
_OVERRIDABLE = {"prompt": {"prompt": True, "tool_ids": True, "tool_ids_strict": True}}
_CLIENT_EVENTS = (
    "conversation_initiation_metadata",
    "ping",
    "audio",
    "interruption",
    "user_transcript",
    "agent_response",
    "agent_response_correction",
    "client_tool_call",
    "agent_tool_response",
    "client_error",
)
_RESOURCE_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")


@dataclass(frozen=True, slots=True)
class ClientTool:
    """Describe a local client tool whose result the conversation waits for."""

    name: str
    description: str
    parameters: dict[str, object] | None = None


@final
class ConvAIControl:
    """Own the authenticated REST connection for one ElevenLabs workspace."""

    __slots__ = ("_credential_id", "_http")
    _credential_id: str
    _http: httpx.Client

    def __new__(cls, api_key: str, *, base_url: str = _API) -> Self:
        """Create a control client without making any requests."""
        if not api_key.strip():
            msg = "ElevenLabs Conversational AI requires an API key"
            raise ValueError(msg)
        self = super().__new__(cls)
        self._credential_id = hashlib.sha256(
            f"{base_url}\0{api_key}".encode()
        ).hexdigest()
        self._http = httpx.Client(
            base_url=base_url, headers={"xi-api-key": api_key}, timeout=10.0
        )
        return self

    @property
    def credential_id(self) -> str:
        """Identify the key and endpoint without retaining the key in config."""
        return self._credential_id

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self._http.close()

    def create_tool(self, tool: ClientTool) -> str:
        """Register a client tool without adding any remote execution capability."""
        schema = {
            "type": "client",
            "name": tool.name,
            "description": tool.description,
            "expects_response": True,
            "parameters": tool.parameters,
        }
        response = self._request("POST", "/tools", {"tool_config": schema})
        return self.resource_id(self._string_field(response, "id"))

    def create_agent(self, tool_ids: tuple[str, ...]) -> str:
        """Provision an agent that requires a signed connection and PCM at 16 kHz."""
        params: dict[str, object] = {
            "name": "Vox voice agent",
            "conversation_config": {
                "agent": {
                    "first_message": "",
                    "language": "en",
                    "prompt": {"tool_ids": list(tool_ids)},
                },
                "tts": {"agent_output_audio_format": "pcm_16000"},
                "asr": {"user_input_audio_format": "pcm_16000"},
                "conversation": {"client_events": list(_CLIENT_EVENTS)},
            },
            "platform_settings": {
                "auth": {"enable_auth": True},
                "overrides": {
                    "conversation_config_override": {
                        "agent": _OVERRIDABLE,
                        "conversation": {"text_only": True},
                    }
                },
            },
        }
        response = self._request("POST", "/agents/create", params)
        return self.resource_id(self._string_field(response, "agent_id"))

    def signed_url(self, agent_id: str) -> str:
        """Fetch a fresh, short-lived WebSocket URL; never cache it."""
        response = self._request(
            "GET",
            "/conversation/get-signed-url",
            params={"agent_id": self.resource_id(agent_id)},
        )
        return self._string_field(response, "signed_url")

    def delete_agent(self, agent_id: str) -> None:
        """Force-delete the agent, treating an absent agent as already deleted."""
        self._force_delete(f"/agents/{self.resource_id(agent_id)}")

    def delete_tool(self, tool_id: str) -> None:
        """Force-delete a Vox-owned tool, treating 404 as already deleted."""
        self._force_delete(f"/tools/{self.resource_id(tool_id)}")

    def _force_delete(self, path: str) -> None:
        self._request("DELETE", path, params={"force": "true"})

    def _request(
        self,
        method: str,
        path: str,
        body: dict[str, object] | None = None,
        *,
        params: dict[str, str] | None = None,
    ) -> httpx.Response:
        try:
            response = self._http.request(
                method, _CONVAI + path, json=body, params=params
            )
        except httpx.HTTPError as exc:
            msg = f"ElevenLabs {method} {path} failed ({type(exc).__name__})"
            raise ConvAIError(msg) from None
        if response.is_success or (method == "DELETE" and response.status_code == 404):
            return response
        msg = f"ElevenLabs {method} {path} returned HTTP {response.status_code}"
        raise ConvAIError(msg)

    @staticmethod
    def _string_field(response: httpx.Response, field: str) -> str:
        try:
            data: object = response.json()
        except (ValueError, RecursionError):
            msg = "ElevenLabs returned invalid JSON"
            raise ConvAIError(msg) from None
        if isinstance(data, dict):
            value = cast("dict[str, object]", data).get(field)
            if isinstance(value, str) and value:
                return value
        msg = f"ElevenLabs reply is missing {field}"
        raise ConvAIError(msg)

    @staticmethod
    def resource_id(value: str) -> str:
        """Validate a remote resource ID before using it in a request path."""
        if not _RESOURCE_ID.fullmatch(value):
            msg = "Invalid ElevenLabs resource ID"
            raise ConvAIError(msg)
        return value
