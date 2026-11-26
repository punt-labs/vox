"""Register client tools and fetch signed conversations through the REST API.

The API origin can be redirected to loopback by a test-only environment
switch, which also replaces the key sent with a placeholder; see
:mod:`punt_vox.voxd.voice_agent.call_overrides`.
"""

from __future__ import annotations

import hashlib
import os
from typing import TYPE_CHECKING, Self, final

from punt_vox.voxd.voice_agent.agent_body import AgentBody
from punt_vox.voxd.voice_agent.call_overrides import CallOverrides
from punt_vox.voxd.voice_agent.fault import ForeignResourceError
from punt_vox.voxd.voice_agent.reply import Reply
from punt_vox.voxd.voice_agent.settings import AgentSettings
from punt_vox.voxd.voice_agent.wire import SIGNED_URL, UNKNOWN_CREATE, Wire

if TYPE_CHECKING:
    from pathlib import Path

    from punt_vox.voxd.voice_agent.contracts import ClientTool

__all__ = ["ConvAIControl"]

_API = "https://api.elevenlabs.io"


@final
class ConvAIControl:
    """Own the authenticated REST connection for one ElevenLabs workspace."""

    __slots__ = ("_credential_id", "_settings", "_wire")
    _credential_id: str
    _settings: AgentSettings
    _wire: Wire

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
        self._wire = Wire(base_url, api_key)
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
        self._wire.close()

    def create_tool(self, tool: ClientTool, marker: str) -> str:
        """Register a client tool, marked as voxd's, with no remote execution."""
        response = self._wire.request("POST", "/tools", AgentBody.tool(tool, marker))
        return response.created_id("id", UNKNOWN_CREATE)

    def create_agent(self, tool_ids: tuple[str, ...], marker: str) -> str:
        """Provision a marked agent that needs a signed connection and 16 kHz PCM."""
        body = AgentBody.create(self._settings, tool_ids, marker)
        response = self._wire.request("POST", "/agents/create", body)
        return response.created_id("agent_id", UNKNOWN_CREATE)

    def registration_fingerprint(self, tools: tuple[ClientTool, ...]) -> str:
        """Identify the provisioned settings and ordered tool catalog."""
        return AgentBody.fingerprint(self._settings, tools)

    def signed_url(self, agent_id: str) -> str:
        """Fetch a fresh, short-lived WebSocket URL; never cache it."""
        response = self._wire.request(
            "GET",
            SIGNED_URL,
            params={"agent_id": Reply.resource_id(agent_id)},
        )
        return response.string("signed_url")

    def delete_agent(self, agent_id: str, marker: str) -> None:
        """Force-delete the agent if it carries *marker*; an absent one is done.

        Raises :class:`ForeignResourceError` for an agent without the marker.
        """
        path = f"/agents/{Reply.resource_id(agent_id)}"
        if self._carries(path, marker, ("name",)):
            self._force_delete(path)

    def delete_tool(self, tool_id: str, marker: str) -> None:
        """Force-delete the tool if it carries *marker*; an absent one is done.

        Raises :class:`ForeignResourceError` for a tool without the marker.
        """
        path = f"/tools/{Reply.resource_id(tool_id)}"
        if self._carries(path, marker, ("tool_config", "description")):
            self._force_delete(path)

    def _carries(self, path: str, marker: str, field: tuple[str, ...]) -> bool:
        """Return whether the resource at *path* has *marker*; False if absent."""
        reply = self._wire.request("GET", path, tolerate_404=True)
        if reply.is_absent():
            return False
        text = reply.text_at(*field)
        if text is not None and marker in text:
            return True
        msg = f"ElevenLabs {path} was not created by this host's voxd; not deleted"
        raise ForeignResourceError(msg)

    def _force_delete(self, path: str) -> None:
        self._wire.request("DELETE", path, params={"force": "true"}, tolerate_404=True)
