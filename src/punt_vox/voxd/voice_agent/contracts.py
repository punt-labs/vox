"""Typed options, registration records, and outgoing ElevenLabs messages."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, NotRequired, TypedDict

if TYPE_CHECKING:
    from punt_vox.voxd.voice_agent.trace import EventTrace

__all__ = ["ClientTool", "SessionOptions"]


@dataclass(frozen=True, slots=True)
class ClientTool:
    """Describe a local client tool whose result the conversation waits for."""

    name: str
    description: str
    # Callers supply arbitrary JSON Schema; the provider allows null for no schema.
    parameters: dict[str, object] | None = None
    # How long the provider waits for the result; None keeps its own default.
    response_timeout_secs: int | None = None


@dataclass(frozen=True, slots=True)
class SessionOptions:
    """Select the prompt and the registered client-tool schemas for a call."""

    prompt: str
    tool_ids: tuple[str, ...]
    text_only: bool = False
    first_message: str | None = None  # None leaves the agent default in effect.


@dataclass(frozen=True, slots=True)
class Registration:
    """Record the remote resources Vox can safely reuse or delete."""

    agent_id: str | None  # Absent before creation and after agent deletion.
    tool_ids: tuple[str, ...]
    configuration_hash: str
    credential_id: str
    # Replaced agents and tools awaiting deletion, retried after each call
    # connects so a slow delete never blocks a call.
    retired_agents: tuple[str, ...] = ()
    retired_tools: tuple[str, ...] = ()


class ToolConfig(TypedDict):
    type: Literal["client"]
    name: str
    description: str
    expects_response: bool
    # The JSON Schema belongs to the tool author; null means no parameters.
    parameters: dict[str, object] | None
    response_timeout_secs: NotRequired[int]


class CreateToolConfig(TypedDict):
    tool_config: ToolConfig


class _SystemToolParams(TypedDict):
    system_tool_type: Literal["end_call", "skip_turn"]
    # How long Skip turn waits before the agent may speak; -1 waits indefinitely.
    wait_timeout_secs: NotRequired[int]


class SystemTool(TypedDict):
    """One of the provider's built-in tools, run on its side, not by a client."""

    type: Literal["system"]
    name: str
    description: str
    params: _SystemToolParams


class BuiltInTools(TypedDict):
    end_call: SystemTool
    skip_turn: SystemTool


class _RegisteredPrompt(TypedDict):
    prompt: str
    llm: str
    timezone: str
    tool_ids: list[str]
    built_in_tools: BuiltInTools


class _RegisteredAgent(TypedDict):
    first_message: str
    language: str
    prompt: _RegisteredPrompt


class _TTSConfig(TypedDict):
    voice_id: str
    agent_output_audio_format: str


class _TurnConfig(TypedDict):
    turn_timeout: int
    turn_eagerness: str


class _ASRConfig(TypedDict):
    user_input_audio_format: str


class _ClientEvents(TypedDict):
    client_events: list[str]


class _AgentConfig(TypedDict):
    agent: _RegisteredAgent
    tts: _TTSConfig
    asr: _ASRConfig
    turn: _TurnConfig
    conversation: _ClientEvents


class _Privacy(TypedDict):
    record_voice: bool


class _Auth(TypedDict):
    enable_auth: bool


class _PromptPermissions(TypedDict):
    prompt: bool
    tool_ids: bool
    tool_ids_strict: bool


class AgentPermissions(TypedDict):
    prompt: _PromptPermissions
    first_message: NotRequired[bool]


class _TextOnly(TypedDict):
    text_only: bool


class _OverridePermissions(TypedDict):
    agent: AgentPermissions
    conversation: _TextOnly


class _Overrides(TypedDict):
    conversation_config_override: _OverridePermissions


class _PlatformSettings(TypedDict):
    auth: _Auth
    overrides: _Overrides
    privacy: _Privacy


class CreateAgentConfig(TypedDict):
    name: str
    conversation_config: _AgentConfig
    platform_settings: _PlatformSettings


class _PromptOverride(TypedDict):
    prompt: str
    tool_ids: list[str]
    tool_ids_strict: bool


class _AgentOverride(TypedDict):
    prompt: _PromptOverride
    first_message: NotRequired[str]


class _SessionOverride(TypedDict):
    agent: _AgentOverride
    conversation: _TextOnly


class SessionStart(TypedDict):
    type: Literal["conversation_initiation_client_data"]
    conversation_config_override: _SessionOverride


class UserMessage(TypedDict):
    type: Literal["user_message"]
    text: str


class ContextualUpdate(TypedDict):
    type: Literal["contextual_update"]
    text: str


class UserAudio(TypedDict):
    user_audio_chunk: str


class ToolResult(TypedDict):
    type: Literal["client_tool_result"]
    tool_call_id: str
    result: str
    is_error: bool


class Pong(TypedDict):
    type: Literal["pong"]
    event_id: int


type ClientMessage = (
    SessionStart | UserMessage | ContextualUpdate | UserAudio | ToolResult | Pong
)
type CreateConfig = CreateAgentConfig | CreateToolConfig
type OptionalTrace = EventTrace | None  # None disables the opt-in trace sink.
