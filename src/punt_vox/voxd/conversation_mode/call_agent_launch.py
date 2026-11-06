"""How the call agent process is launched: its argv, its environment, its rules.

One ``pi --mode rpc`` process per call, launched read-only for its whole life
(the call-agent Z model's second invariant): the tool allowlist is fixed at
launch, never toggled per turn, so there is no window in which a write tool
exists. Everything else that could widen that surface is switched off at
launch too -- extensions (one of which, ``biff-bridge``, was found to stall
the RPC stream outright), pi's built-in MCP support (``--tools`` keeps MCP
tools unless told otherwise), skills, prompt templates, project-local pi
config (``--no-approve``), and the repo's AGENTS.md/CLAUDE.md (the snapshot
carries the context; those files instruct a coding agent with write access).

The environment is an allowlist (:class:`~.call_agent_env.CallAgentEnv`):
nothing secret from the developer's shell rides along by default.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Self, final

from punt_vox.voxd.conversation_mode.call_agent_env import CallAgentEnv
from punt_vox.voxd.conversation_mode.ripgrep import Ripgrep

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
    "--no-context-files",
)

# The vox-owned pi extension that blocks a denied tool call before it runs.
# The one extension a call agent loads: an explicit -e path still loads under
# --no-extensions, which keeps every discovered and built-in one off.
SECRET_GUARD = Path(__file__).with_name("pi_extension") / "secret_guard.js"

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
        if not SECRET_GUARD.is_file():
            msg = f"the call agent's secret guard is missing: {SECRET_GUARD}"
            raise ValueError(msg)
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
            "--extension",
            str(SECRET_GUARD),
            "--tools",
            ",".join(READ_ONLY_TOOLS),
            "--append-system-prompt",
            self.system_prompt(),
        )

    def system_prompt(self) -> str:
        """Return the text appended to pi's own system prompt for a call."""
        return f"{_ROLE}\n\n{self._policy.prompt_clause()}"

    def require_ripgrep(self, parent: Mapping[str, str]) -> Path:
        """Return the ``rg`` the guard will run in the child started from *parent*.

        Raises :class:`~.call_agent_errors.CallAgentSpawnError` when there is
        none: every directory grep of the call would be refused.
        """
        return Ripgrep.locate(self.env(parent))

    def env(self, parent: Mapping[str, str]) -> dict[str, str]:
        """Return the child's environment: allowlisted, plus the guard's config."""
        return CallAgentEnv.allowlisted(parent) | self._policy.guard_env()
