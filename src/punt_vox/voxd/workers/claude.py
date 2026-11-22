"""Claude Code is one harness profile, with inherited user permissions."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Self, final

from punt_vox.process_env import ProcessEnv
from punt_vox.voxd.hook_context_store import HookContextStore
from punt_vox.voxd.workers.trust import ClaudeTrust


@final
class ClaudeProfile:
    """Launch Claude with a session identity shared with the hook store."""

    __slots__ = ("_store", "_trust")
    _store: HookContextStore
    _trust: ClaudeTrust
    submit_key = "Enter"
    interrupt_key = "Escape"
    ENV_NAMES = frozenset(
        {"CLAUDE_CONFIG_DIR", "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"}
    )

    def __new__(cls, store: HookContextStore) -> Self:
        """Bind the daemon's shared live context store."""
        self = super().__new__(cls)
        self._store = store
        self._trust = ClaudeTrust()
        return self

    def argv(self, worker_id: str, task: str) -> list[str]:
        """Use interactive Claude, never a separate permission profile."""
        return ["claude", "--session-id", worker_id, "--", task]

    def environment(self, parent: Mapping[str, str]) -> dict[str, str]:
        """Admit base process variables and explicitly named Claude credentials."""
        return {
            k: v
            for k, v in parent.items()
            if ProcessEnv.admits(k) or k in self.ENV_NAMES
        }

    def prepare(self, worktree: Path) -> None:
        """Accept trust only for this exact absolute worktree."""
        self._trust.update(worktree, accepted=True)

    def cleanup(self, worktree: Path) -> None:
        """Remove the temporary trust flag, retaining work product."""
        self._trust.update(worktree, accepted=False)

    def progress(self, worker_id: str) -> dict[str, object]:
        """SessionStart, not pane existence, establishes readiness."""
        try:
            return dict(self._store.snapshot(worker_id))
        except ValueError:
            return {"session_started": False}
