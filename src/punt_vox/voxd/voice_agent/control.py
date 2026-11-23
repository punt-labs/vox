"""Register client tools and fetch signed conversations through the REST API."""

from __future__ import annotations

import hashlib
import json
import re
from typing import TYPE_CHECKING, Self, cast, final

import httpx

from punt_vox.voxd.voice_agent._decode import EventDecoder
from punt_vox.voxd.voice_agent.fault import ConvAIError

if TYPE_CHECKING:
    from punt_vox.voxd.voice_agent.contracts import (
        AgentPermissions,
        ClientTool,
        CreateAgentConfig,
        CreateConfig,
        CreateToolConfig,
    )

__all__ = ["ConvAIControl"]

_API = "https://api.elevenlabs.io"
_CONVAI = "/v1/convai"
_SIGNED_URL = "/conversation/get-signed-url"
_RESOURCE_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
_UNKNOWN_CREATE = "; check the ElevenLabs workspace for an unrecorded resource"
_OVERRIDABLE: AgentPermissions = {
    "prompt": {"prompt": True, "tool_ids": True, "tool_ids_strict": True},
    "first_message": True,
}


@final
class ConvAIControl:
    """Own the authenticated REST connection for one ElevenLabs workspace."""

    __slots__ = ("_credential_id", "_http")
    _credential_id: str
    _http: httpx.Client

    def __new__(cls, api_key: str, *, base_url: str = _API) -> Self:
        """Construct off the event loop because key derivation is synchronous."""
        if not api_key.strip():
            msg = "ElevenLabs Conversational AI requires an API key"
            raise ValueError(msg)
        self = super().__new__(cls)
        self._credential_id = hashlib.scrypt(
            api_key.encode(),
            salt=f"vox:elevenlabs:convai:{base_url}".encode(),
            n=2**14,
            r=8,
            p=5,
        ).hex()
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
        response = self._request("POST", "/tools", self._tool_config(tool))
        return self._created_id(response, "id")

    def create_agent(self, tool_ids: tuple[str, ...]) -> str:
        """Provision an agent that requires a signed connection and PCM at 16 kHz."""
        response = self._request("POST", "/agents/create", self._agent_config(tool_ids))
        return self._created_id(response, "agent_id")

    @classmethod
    def _created_id(cls, response: httpx.Response, field: str) -> str:
        try:
            return cls.resource_id(cls._string_field(response, field))
        except ConvAIError as exc:
            raise ConvAIError(f"{exc}{_UNKNOWN_CREATE}") from None

    @classmethod
    def registration_fingerprint(cls, tools: tuple[ClientTool, ...]) -> str:
        """Identify the provisioned settings and ordered tool catalog."""
        names = {tool.name for tool in tools}
        if "" in names or len(names) != len(tools):
            msg = "ElevenLabs client-tool names must be nonempty and unique"
            raise ValueError(msg)
        settings = (cls._agent_config(()), [cls._tool_config(tool) for tool in tools])
        return hashlib.sha256(
            json.dumps(settings, sort_keys=True, allow_nan=False).encode()
        ).hexdigest()

    @staticmethod
    def _tool_config(tool: ClientTool) -> CreateToolConfig:
        return {
            "tool_config": {
                "type": "client",
                "name": tool.name,
                "description": tool.description,
                "expects_response": True,
                "parameters": tool.parameters,
            }
        }

    @staticmethod
    def _agent_config(tool_ids: tuple[str, ...]) -> CreateAgentConfig:
        return {
            "name": "Vox voice agent",
            "conversation_config": {
                "agent": {
                    "first_message": "",
                    "language": "en",
                    "prompt": {"tool_ids": list(tool_ids)},
                },
                "tts": {"agent_output_audio_format": "pcm_16000"},
                "asr": {"user_input_audio_format": "pcm_16000"},
                "conversation": {"client_events": list(EventDecoder.subscriptions())},
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

    def signed_url(self, agent_id: str) -> str:
        """Fetch a fresh, short-lived WebSocket URL; never cache it."""
        response = self._request(
            "GET",
            _SIGNED_URL,
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
        body: CreateConfig | None = None,  # Only create requests have a JSON body.
        *,
        params: dict[str, str] | None = None,  # Only GET and DELETE take query params.
    ) -> httpx.Response:
        try:
            response = self._http.request(
                method, _CONVAI + path, json=body, params=params
            )
        except httpx.HTTPError as exc:
            msg = f"ElevenLabs {method} {path} failed ({type(exc).__name__})"
            if method == "POST":
                msg += _UNKNOWN_CREATE
            raise ConvAIError(msg) from None
        if response.is_success or (method == "DELETE" and response.status_code == 404):
            return response
        msg = self._status_message(method, path, response.status_code)
        raise ConvAIError(msg)

    @staticmethod
    def _status_message(method: str, path: str, code: int) -> str:
        if code == 404 and path == _SIGNED_URL:
            return (
                "The ElevenLabs agent no longer exists; tear down the Vox voice "
                "agent to reprovision (HTTP 404)"
            )
        message = f"ElevenLabs {method} {path} returned HTTP {code}"
        if code in (401, 403):
            return f"{message}; check the key's ElevenLabs Agents access"
        return message

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
