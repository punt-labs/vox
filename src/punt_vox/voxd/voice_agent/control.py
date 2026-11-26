"""Register client tools and fetch signed conversations through the REST API.

The API origin can be redirected to loopback by a test-only environment
switch, which also replaces the key sent with a placeholder; see
:mod:`punt_vox.voxd.voice_agent.call_overrides`.
"""

from __future__ import annotations

import hashlib
import os
import re
from typing import TYPE_CHECKING, Self, cast, final

import httpx

from punt_vox.voxd.voice_agent.agent_body import AgentBody
from punt_vox.voxd.voice_agent.call_overrides import CallOverrides
from punt_vox.voxd.voice_agent.fault import ConvAIError
from punt_vox.voxd.voice_agent.settings import AgentSettings

if TYPE_CHECKING:
    from pathlib import Path

    from punt_vox.voxd.voice_agent.contracts import (
        ClientTool,
        CreateConfig,
    )

__all__ = ["ConvAIControl"]

_API = "https://api.elevenlabs.io"
_CONVAI = "/v1/convai"
_SIGNED_URL = "/conversation/get-signed-url"
_RESOURCE_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
_UNKNOWN_CREATE = "; check the ElevenLabs workspace for an unrecorded resource"


@final
class ConvAIControl:
    """Own the authenticated REST connection for one ElevenLabs workspace."""

    __slots__ = ("_credential_id", "_http", "_settings")
    _credential_id: str
    _http: httpx.Client
    _settings: AgentSettings

    def __new__(
        cls,
        api_key: str,
        *,
        base_url: str | None = None,  # None reads the test override, else the API.
        settings: AgentSettings | None = None,  # None: the minimal settings.
    ) -> Self:
        """Construct off the event loop because key derivation is synchronous."""
        if not api_key.strip():
            msg = "ElevenLabs Conversational AI requires an API key"
            raise ValueError(msg)
        if base_url is None:
            base_url, api_key = CallOverrides.rest(_API, api_key)
        self = super().__new__(cls)
        self._settings = AgentSettings.minimal() if settings is None else settings
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

    @classmethod
    def from_environment(cls, settings: AgentSettings | None = None) -> Self:
        """Construct from ``ELEVENLABS_API_KEY``, or from the REST override alone.

        The override is resolved first: it supplies the placeholder key, so
        with it in effect no key needs to be set. Without it, a missing key is
        a :class:`ValueError` naming the variable.
        """
        base_url, api_key = CallOverrides.rest(
            _API, os.environ.get("ELEVENLABS_API_KEY", "")
        )
        if not api_key:
            msg = "voxd has no ELEVENLABS_API_KEY for a voice call"
            raise ValueError(msg)
        return cls(api_key, base_url=base_url, settings=settings)

    @property
    def credential_id(self) -> str:
        """Identify the key and endpoint without retaining the key in config."""
        return self._credential_id

    @staticmethod
    def registration_path(default: Path) -> Path:
        """Return the registration path: *default*, or its override sibling."""
        return CallOverrides.registration_path(default)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self._http.close()

    def create_tool(self, tool: ClientTool) -> str:
        """Register a client tool without adding any remote execution capability."""
        response = self._request("POST", "/tools", AgentBody.tool(tool))
        return self._created_id(response, "id")

    def create_agent(self, tool_ids: tuple[str, ...]) -> str:
        """Provision an agent that requires a signed connection and PCM at 16 kHz."""
        body = AgentBody.create(self._settings, tool_ids)
        response = self._request("POST", "/agents/create", body)
        return self._created_id(response, "agent_id")

    @classmethod
    def _created_id(cls, response: httpx.Response, field: str) -> str:
        try:
            return cls.resource_id(cls._string_field(response, field))
        except ConvAIError as exc:
            raise ConvAIError(f"{exc}{_UNKNOWN_CREATE}") from None

    def registration_fingerprint(self, tools: tuple[ClientTool, ...]) -> str:
        """Identify the provisioned settings and ordered tool catalog."""
        return AgentBody.fingerprint(self._settings, tools)

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
