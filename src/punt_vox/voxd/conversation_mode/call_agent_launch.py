"""How the call agent process is launched: its argv, its environment, its rules.

One ``pi --mode rpc`` process per call, launched read-only for its whole life
(the call-agent Z model's second invariant): the tool allowlist is fixed at
launch, never toggled per turn, so there is no window in which a write tool
exists. Everything else that could widen that surface is switched off at
launch too -- extensions (one of which, ``biff-bridge``, was found to stall
the RPC stream outright), pi's built-in MCP support (``--tools`` keeps MCP
tools unless told otherwise), skills, prompt templates, and any
project-local pi configuration the repo might carry (``--no-approve``).

The environment is an allowlist (:class:`~.call_agent_env.CallAgentEnv`):
nothing secret from the developer's shell rides along by default.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Self, final

from punt_vox.voxd.conversation_mode.call_agent_env import CallAgentEnv

if TYPE_CHECKING:
    from collections.abc import Mapping

    from punt_vox.voxd.conversation_mode.secret_path_policy import SecretPathPolicy

__all__ = ["CallAgentLaunch"]

# Exactly pi's read-only built-ins. No bash, edit, or write -- ever.
READ_ONLY_TOOLS: tuple[str, ...] = ("read", "grep", "find", "ls")

_SURFACE_OFF: tuple[str, ...] = (
    "--no-session",
    "--no-extensions",
    "--no-mcp",
    "--no-skills",
    "--no-prompt-templates",
    "--no-approve",
)

_ROLE = (
    "You are on a live voice call with a developer. Everything you write is "
    "converted to speech, so answer in short, plain spoken sentences: no "
    "markdown, no code blocks, no lists, no file contents read out at length. "
    "You can read the repository with read, grep, find, and ls, and you "
    "cannot change it -- if a change is needed, say what it is so the "
    "developer's main session can make it after the call."
)


@final
class CallAgentLaunch:
    """The argv and environment one call agent process is started with."""

    __slots__ = ("_pi_bin", "_policy")
    _pi_bin: str
    _policy: SecretPathPolicy

    def __new__(cls, policy: SecretPathPolicy, *, pi_bin: str = "pi") -> Self:
        self = super().__new__(cls)
        self._policy = policy
        self._pi_bin = pi_bin
        return self

    @property
    def program(self) -> str:
        return self._pi_bin

    @property
    def policy(self) -> SecretPathPolicy:
        return self._policy

    def argv(self) -> tuple[str, ...]:
        """Return the full command line, program first."""
        return (
            self._pi_bin,
            "--mode",
            "rpc",
            *_SURFACE_OFF,
            "--tools",
            ",".join(READ_ONLY_TOOLS),
            "--append-system-prompt",
            self.system_prompt(),
        )

    def system_prompt(self) -> str:
        """Return the text appended to pi's own system prompt for a call."""
        return f"{_ROLE}\n\n{self._policy.prompt_clause()}"

    @staticmethod
    def env(parent: Mapping[str, str]) -> dict[str, str]:
        """Return the child's environment: the allowlisted part of *parent*."""
        return CallAgentEnv.allowlisted(parent)
