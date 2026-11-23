"""Lazily bind the call's account-backed agent outside the RPC lifecycle."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Protocol, Self, final

from punt_vox.voxd.voice_agent.agent import VoiceAgent
from punt_vox.voxd.voice_agent.control import ConvAIControl

if TYPE_CHECKING:
    from punt_vox.voxd.voice_agent.contracts import ClientTool


class AgentConnector(Protocol):
    """Provision or reuse an agent and sign a conversation URL."""

    def connection(self, tools: tuple[ClientTool, ...]) -> tuple[str, tuple[str, ...]]:
        """Return a signed URL and the registered tool IDs."""
        ...


@final
class CallConnector:
    """Read account configuration only when a call is requested."""

    __slots__ = ("_agent",)
    _agent: AgentConnector | None  # None until the first call reads the key.

    def __new__(cls, agent: AgentConnector | None = None) -> Self:
        """Bind an injected agent, or defer provisioning."""
        self = super().__new__(cls)
        self._agent = agent
        return self

    def agent(self) -> AgentConnector:
        """Return the persistent connector or report a missing credential."""
        if self._agent is None:
            key = os.environ.get("ELEVENLABS_API_KEY", "")
            if not key:
                raise ValueError("voxd has no ELEVENLABS_API_KEY for a voice call")
            self._agent = VoiceAgent(ConvAIControl(key))
        return self._agent
