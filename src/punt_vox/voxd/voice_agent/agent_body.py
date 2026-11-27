"""Render the ElevenLabs create bodies and the fingerprint from the settings."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Final, final

from punt_vox.voxd.voice_agent._decode import EventDecoder
from punt_vox.voxd.voice_agent.settings import (
    LLM,
    SKIP_TURN_WAIT,
    TURN_EAGERNESS,
    TURN_TIMEOUT,
    VOICE_ID,
)

if TYPE_CHECKING:
    from punt_vox.voxd.voice_agent.contracts import (
        AgentPermissions,
        BuiltInTools,
        ClientTool,
        CreateAgentConfig,
        CreateToolConfig,
    )
    from punt_vox.voxd.voice_agent.settings import AgentSettings

__all__ = ["AgentBody"]

# What a call may replace: its prompt, its tool list and its first message.
_OVERRIDABLE: Final[AgentPermissions] = {
    "prompt": {"prompt": True, "tool_ids": True, "tool_ids_strict": True},
    "first_message": True,
}
# The provider ends the call itself when the agent runs end_call, and holds the
# turn when it runs skip_turn; the daemon only hears the acknowledgement.
_BUILT_IN_TOOLS: Final[BuiltInTools] = {
    "end_call": {
        "type": "system",
        "name": "end_call",
        "description": (
            "End the call after the person says goodbye or asks to end the call "
            "or hang up, once you have said one short closing sentence."
        ),
        "params": {"system_tool_type": "end_call"},
    },
    "skip_turn": {
        "type": "system",
        "name": "skip_turn",
        "description": (
            "Stay silent and let the person keep the floor when they ask for a "
            "moment, are thinking aloud, or are talking to someone else."
        ),
        "params": {
            "system_tool_type": "skip_turn",
            "wait_timeout_secs": SKIP_TURN_WAIT,
        },
    },
}
_AGENT_NAME: Final = "Vox voice agent"
_FINGERPRINT_ZONE: Final = "UTC"  # A travelled host must not rebuild the agent.


@final
class AgentBody:
    """The one place the agent's request bodies are written down."""

    __slots__ = ()

    @staticmethod
    def tool(
        tool: ClientTool,
        marker: str | None = None,  # None is the unmarked form the fingerprint hashes.
    ) -> CreateToolConfig:
        """Return the create body of one client tool, marked as voxd's if asked."""
        description = (
            tool.description if marker is None else f"{tool.description}\n\n{marker}"
        )
        config: CreateToolConfig = {
            "tool_config": {
                "type": "client",
                "name": tool.name,
                "description": description,
                "expects_response": True,
                "parameters": tool.parameters,
            }
        }
        if tool.response_timeout_secs is not None:
            config["tool_config"]["response_timeout_secs"] = tool.response_timeout_secs
        return config

    @staticmethod
    def create(
        settings: AgentSettings,
        tool_ids: tuple[str, ...],
        marker: str | None = None,  # None is the unmarked form the fingerprint hashes.
    ) -> CreateAgentConfig:
        """Return the create-agent body: signed access, PCM at 16 kHz, no recording."""
        name = _AGENT_NAME if marker is None else f"{_AGENT_NAME} {marker}"
        return {
            "name": name,
            "conversation_config": {
                "agent": {
                    "first_message": settings.first_message,
                    "language": "en",
                    "prompt": {
                        "prompt": settings.prompt,
                        "llm": LLM,
                        "timezone": settings.timezone,
                        "tool_ids": list(tool_ids),
                        "built_in_tools": _BUILT_IN_TOOLS,
                    },
                },
                "tts": {
                    "voice_id": VOICE_ID,
                    "agent_output_audio_format": "pcm_16000",
                },
                "asr": {"user_input_audio_format": "pcm_16000"},
                "turn": {
                    "turn_timeout": TURN_TIMEOUT,
                    "turn_eagerness": TURN_EAGERNESS,
                },
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
                "privacy": {"record_voice": False},
            },
        }

    @staticmethod
    def fingerprint(settings: AgentSettings, tools: tuple[ClientTool, ...]) -> str:
        """Identify the provisioned settings and ordered tool catalog.

        The zone is left out: the per-call prompt names the time, so moving the
        laptop must not build a new agent.
        """
        names = {tool.name for tool in tools}
        if "" in names or len(names) != len(tools):
            msg = "ElevenLabs client-tool names must be nonempty and unique"
            raise ValueError(msg)
        body = AgentBody.create(settings.with_zone(_FINGERPRINT_ZONE), ())
        rendered = (body, [AgentBody.tool(tool) for tool in tools])
        return hashlib.sha256(
            json.dumps(rendered, sort_keys=True, allow_nan=False).encode()
        ).hexdigest()
